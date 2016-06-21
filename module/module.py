#!/usr/bin/python
# -*- coding: utf-8 -*-


# Copyright (C) 2009-2015:
#    Gabes Jean, naparuba@gmail.com
#    Gerhard Lausser, Gerhard.Lausser@consol.de
#    Gregory Starck, g.starck@gmail.com
#    Hartmut Goebel, h.goebel@goebel-consult.de
#    Frederic Mohier, frederic.mohier@gmail.com
#    Alexandre Le Mao, alexandre.lemao@gmail.com
#    Maanas Royy, m4manas@gmail.com
#
# This file is part of Shinken.
#
# Shinken is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Shinken is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Shinken.  If not, see <http://www.gnu.org/licenses/>.


"""
This class is for pushing an Elasticsearch cluster log to another Elasticsearch instance.
It uses shinken to perform the action.

"""
import os
import time
from datetime import date, datetime, timedelta
import re
import sys
import traceback

from elasticsearch import Elasticsearch,helpers,ElasticsearchException,TransportError
import curator

from shinken.objects.service import Service
from shinken.modulesctx import modulesctx

from .log_line import (
    Logline,
    LOGCLASS_INVALID
)

from shinken.basemodule import BaseModule
from shinken.objects.module import Module
from shinken.log import logger

from collections import deque

properties = {
    'daemons': ['broker',],
    'type': 'elastic-mon',
    'external': True,
    'phases': ['running'],
}

# called by the plugin manager
def get_instance(plugin):
    logger.info("[elastic-lmon] got an instance of ElasticMon for module: %s", plugin.get_name())
    instance = ElasticMon(plugin)
    return instance

# Constants: Module Status
OK = 0
WARNING = 1
CRITICAL = 2
UNKNOWN = 3

# Constants: ES Status
CONNECTED = 1
DISCONNECTED = 2
SWITCHING = 3

class ElasticMonError(Exception):
    pass

class ElasticMon():

    def __init__(self, mod_conf=None):
        # BaseModule.__init__(self, mod_conf)
        self.interrupted = None

        self.hosts = getattr(mod_conf, 'hosts', 'localhost:9200')
        logger.info('[elastic-mon] Hosts: %s', self.hosts)

        self.index_hosts = getattr(mod_conf, 'index_hosts', 'localhost:9200')
        logger.info('[elastic-mon] Index Hosts: %s', self.hosts)

        self.index_prefix = getattr(mod_conf, 'index_prefix', 'elasticmon')
        logger.info('[elastic-mon] Index: %s', self.index_prefix)

        self.timeout = getattr(mod_conf, 'timeout', '20')
        logger.info('[elastic-mon] Timeout: %s', self.timeout)

        self.commit_period = int(getattr(mod_conf, 'commit_period', '60'))
        logger.info('[elastic-mon] periodical commit period: %ds', self.commit_period)

        self.commit_volume = int(getattr(mod_conf, 'commit_volume', '200'))
        logger.info('[elastic-mon] periodical commit volume: %d lines', self.commit_volume)

        self.cluster_test_period = int(getattr(mod_conf, 'cluster_test_period', '0'))
        logger.info('[elastic-mon] periodical ES Cluster connection test period: %ds', self.cluster_test_period)

        max_logs_age = getattr(mod_conf, 'max_logs_age', '1m')
        maxmatch = re.match(r'^(\d+)([dwmy]*)$', max_logs_age)
        if not maxmatch:
            logger.error('[elastic-mon] Wrong format for max_logs_age. Must be <number>[d|w|m|y] or <number> and not %s' % max_logs_age)
            return None
        else:
            if not maxmatch.group(2):
                self.max_logs_age = int(maxmatch.group(1))
            elif maxmatch.group(2) == 'd':
                self.max_logs_age = int(maxmatch.group(1))
            elif maxmatch.group(2) == 'w':
                self.max_logs_age = int(maxmatch.group(1)) * 7
            elif maxmatch.group(2) == 'm':
                self.max_logs_age = int(maxmatch.group(1)) * 31
            elif maxmatch.group(2) == 'y':
                self.max_logs_age = int(maxmatch.group(1)) * 365
        logger.info('[elastic-mon] max_logs_age: %s', self.max_logs_age)

        self.is_cluster_connected = DISCONNECTED
        self.is_index_connected = DISCONNECTED

        self.next_logs_rotation = time.time() + 5000
        
        self.services_cache = {}
        
        self.logs_cache = deque()
        

    def load(self, app):
        self.app = app


    def init(self):
        return True


    def open(self):
        """
        Connect to ES cluster.
        """
        logger.info("[elastic-mon] trying to connect to ES Cluster: %s", self.hosts)
        self.es = Elasticsearch(self.hosts.split(','), timeout=int(self.timeout))
        try:
            self.es.cluster.health()
            logger.info("[elastic-mon] connected to the ES Cluster: %s", self.hosts)
            self.is_cluster_connected = CONNECTED
        except TransportError, exp:
            logger.error("[elastic-mon] Cluster is not available: %s", str(exp))
            self.is_cluster_connected = DISCONNECTED
            return False

        logger.info("[elastic-mon] trying to connect to ES Index: %s", self.index_hosts)
        self.index_es = Elasticsearch(self.index_hosts.split(','), timeout=int(self.timeout))
        try:
            self.index_es.cluster.health()
            logger.info("[elastic-mon] connected to the ES Cluster: %s", self.index_hosts)
            self.is_index_connected = CONNECTED
            self.next_logs_rotation = time.time()
        except TransportError, exp:
            logger.error("[elastic-mon] Index is not available: %s", str(exp))
            self.is_index_connected = DISCONNECTED
            return False
        
        return True

    def close(self):
        self.is_cluster_connected = DISCONNECTED
        self.is_index_connected = DISCONNECTED
        #connections.remove_connection('shinken')
        logger.info('[elastic-mon] Cluster connection closed')

    def commit(self):
        pass

    def create_index(self,index):
        try:
            logger.debug("[elastic-mon] Creating index %s ...", index)
            self.index_es.indices.create(index)
        except ElasticsearchException, exp:
            logger.error("[elastic-mon] exception while creating index %s: %s", index, str(exp))

    def is_index_exists(self,index):
        if not self.is_index_connected == CONNECTED:
            try:
                if self.index_es.indices.exists(index):
                    return True
                else:
                    return False
            except ElasticsearchException, exp:
                logger.error("[elastic-mon] exception while checking the existance of the index %s: %s", index, str(exp))

        return True    

    def rotate_logs(self):
        """
        We will delete indices older than configured maximum age.
        """
        
        if not self.is_index_connected == CONNECTED:
            if not self.open():
                self.next_logs_rotation = time.time() + 600
                logger.info("[elastic-mon] log rotation failed, next log rotation at %s " % time.asctime(time.localtime(self.next_logs_rotation)))
                return

        logger.info("[elastic-mon] rotating logs ...")

        now = time.time()
        today = date.today()
        today0000 = datetime(today.year, today.month, today.day, 0, 0, 0)
        today0005 = datetime(today.year, today.month, today.day, 0, 5, 0)
        oldest = today0000 - timedelta(days=self.max_logs_age)
        if now < time.mktime(today0005.timetuple()):
            next_rotation = today0005
        else:
            next_rotation = today0005 + timedelta(days=1)

        try: 
            indices = curator.get_indices(self.index_es)
            filter_list = []
            filter_list.append(curator.build_filter(kindOf='prefix', value=self.index_prefix))
            filter_list.append(
                curator.build_filter(
                    kindOf='older_than', value=self.max_logs_age, time_unit='days',
                    timestring='%Y.%m.%d'
                )
            )
            working_list = indices
            for filter in filter_list:
                working_list = curator.apply_filter(working_list, **filter)

            curator.delete(self.index_es,working_list)        
            logger.info("[elastic-mon] removed %d logs older than %s days.", working_list, self.max_logs_age)

        except Exception, exp:
            logger.error("[elastic-mon] Exception while rotating indices: %s", str(exp))

        if now < time.mktime(today0005.timetuple()):
            next_rotation = today0005
        else:
            next_rotation = today0005 + timedelta(days=1)

        self.next_logs_rotation = time.mktime(next_rotation.timetuple())
        logger.info("[elastic-mon] next log rotation at %s " % time.asctime(time.localtime(self.next_logs_rotation)))


    def commit_logs(self):
        """
        Periodically called (commit_period), this method retreives the Elasticsearch cluster stats and pushed in Elasticsearch index
        """
        if not self.logs_cache:
            return

        if not self.is_cluster_connected == CONNECTED && not self.is_index_connected:
            if not self.open():
                logger.warning("[elastic-mon] log commiting failed")
                logger.warning("[elastic-mon] %d lines to insert in the index", len(self.logs_cache))
                return

        logger.debug("[elastic-mon] commiting ...")
        
        # Retrive cluster stats and push in index
        stats = self.es.cluster.stats()
        ts = stats['timestamp']
        ts = datetime.fromtimestamp(ts/1000.0)
        stats['timestamp'] = ts
        resp = self.index_es.index(index_name, doc_type='cluster_stats', body=stats)
        
        
    def main(self):
        self.set_proctitle(self.name)
        self.set_exit_handler()

        # Open database connection
        self.open()

        db_commit_next_time = time.time()
        db_test_connection = time.time()

        while not self.interrupted:
            now = time.time()
            d = date.today()
            index_name = self.index_prefix + '-' + d.strftime('%Y.%m.%d')

            # DB connection test ?
            if self.cluster_test_period and db_test_connection < now:
                logger.debug("[elastic-mon] Testing cluster connection ...")
                # Test connection every 5 seconds ...
                db_test_connection = now + self.cluster_test_period
                if self.is_cluster_connected == DISCONNECTED && self.is_index_connected == DISCONNECTED:
                    self.open()

            # Create index ?
            if not self.is_index_exists(index_name):
                self.create_index(index_name)

            # Logs commit ?
            if db_commit_next_time < now:
                logger.debug("[elastic-mon] Logs commit time ...")
                # Commit periodically ...
                db_commit_next_time = now + self.commit_period
                self.commit_logs()

            # Logs rotation ?
            if self.next_logs_rotation < now:
                logger.debug("[elastic-mon] Logs rotation time ...")
                self.rotate_logs()

        # Close cluster connection
        self.close()

