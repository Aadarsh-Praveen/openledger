{% macro bronze_iceberg_scan() %}
{#
  The bronze read-path expression: table root (not metadata.json path),
  allow_moved_paths for the relocated warehouse, unsafe_enable_version_guessing
  set separately at the connection level via profiles.yml (PyIceberg's
  SqlCatalog never writes a version-hint.text file). Every MODEL reads bronze
  via {{ source('bronze', 'service_requests') }}, not this macro directly —
  dbt-duckdb's SourceConfig.external_location renders its Jinja in a context
  where custom project macros are not yet in scope (verified empirically: a
  {{ bronze_iceberg_scan() }} call there raised "macro is undefined"), so
  models/staging/_sources.yml inlines the equivalent expression using
  {{ var("bronze_warehouse_root") }} directly instead. Both this macro and
  that inline expression read the same one project var, so there remains
  exactly one place to change the actual path — this macro exists for any
  ad-hoc analysis/script that needs the same scan expression outside a
  dbt source context, where macro calls do resolve normally.
#}
iceberg_scan('{{ var("bronze_warehouse_root") }}', allow_moved_paths => true)
{% endmacro %}
