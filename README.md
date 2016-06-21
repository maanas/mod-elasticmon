# mod-elasticmon
A shinken broker module to monitor elasticsearch cluster, pushed data back to ES instance

## Main features

This module issue api call to _cluster/stats and pulls the json for cluster stats. This json is store in ElasticSearch index (elasticmon-*). The logs are then available for an application such as Kibana.


## Requirements
Use elasticsearch version > 2.0.0 and elasticsearch-curator > 3.4.0

```
   pip install elasticsearch>=2.0
   pip install elasticsearch-curator>=3.4
```


## Enabling Elastic logs

To use the elasticmon module you must declare it in your broker configuration.
```
   define broker {
      ...

      modules        ..., elasticmon

   }
```


The module configuration is defined in the file: `elasticmon.cfg`.

Default configuration needs to be tuned up to your ElasticSearch configuration.

```
## Module:      elasticmon
## Loaded by:   Broker
# Store the Elasticsearch cluster logs in Elasticsearch
#
define module {
    module_name     elasticmon
    module_type     elasticmon

    # ElasticSearch cluster connection
    # EXAMPLE
    # hosts es1.example.net:9200,es2.example.net:9200,es3.example.net:9200
    hosts           localhost:9200

    # ElasticSearch instance connection
    # EXAMPLE
    # hosts es1.example.net:9200,es2.example.net:9200,es3.example.net:9200
    index_hosts           localhost:9200

    # The prefix of the index where to store the logs.
    # There will be one indexe per day: shinken-YYYY.MM.DD
    index_prefix    elasticmon

    # The timeout connection to the ElasticSearch Cluster
    timeout         20

    # Commit period
    # Every commit_period seconds, the module retrieves the cluster stats and store in the index
    # Default is to commit every 60 seconds
    commit_period     60

    # Logs rotation
    #
    # Remove indices older than the specified value
    # Value is specified as :
    # 1d: 1 day
    # 3m: 3 months ...
    # d = days, w = weeks, m = months, y = years
    # Default is 1 month
    max_logs_age    1m

    
}

```

## Credits
[Mod Elastic Logs](https://github.com/descrepes/mod-elastic-logs)