"""
Authoritative bronze schema, per C1.2 (docs/source-notes.md). 48 dataset columns +
the :updated_at system field. All `number` columns map to double (bronze-fidelity
choice, not a guess at int-vs-float per column — see C1.2 rationale). All
`calendar_date` columns are naive timestamps, assumed Eastern local per C1.1b's
timezone evidence. `:updated_at` is the one genuine UTC-aware field.
"""

from datetime import datetime

import pyarrow as pa
from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType, TimestampType, TimestamptzType

# (field_id, api_name, kind) — kind in {"string", "timestamp", "double", "point", "updated_at"}
COLUMNS = [
    (1, "unique_key", "string"),
    (2, "created_date", "timestamp"),
    (3, "closed_date", "timestamp"),
    (4, "agency", "string"),
    (5, "agency_name", "string"),
    (6, "complaint_type", "string"),
    (7, "descriptor", "string"),
    (8, "descriptor_2", "string"),
    (9, "location_type", "string"),
    (10, "incident_zip", "string"),
    (11, "incident_address", "string"),
    (12, "street_name", "string"),
    (13, "cross_street_1", "string"),
    (14, "cross_street_2", "string"),
    (15, "intersection_street_1", "string"),
    (16, "intersection_street_2", "string"),
    (17, "address_type", "string"),
    (18, "city", "string"),
    (19, "landmark", "string"),
    (20, "facility_type", "string"),
    (21, "status", "string"),
    (22, "due_date", "timestamp"),
    (23, "resolution_description", "string"),
    (24, "resolution_action_updated_date", "timestamp"),
    (25, "community_board", "string"),
    (26, "council_district", "string"),
    (27, "police_precinct", "string"),
    (28, "bbl", "string"),
    (29, "borough", "string"),
    (30, "x_coordinate_state_plane", "double"),
    (31, "y_coordinate_state_plane", "double"),
    (32, "open_data_channel_type", "string"),
    (33, "park_facility_name", "string"),
    (34, "park_borough", "string"),
    (35, "vehicle_type", "string"),
    (36, "taxi_company_borough", "string"),
    (37, "taxi_pick_up_location", "string"),
    (38, "bridge_highway_name", "string"),
    (39, "bridge_highway_direction", "string"),
    (40, "road_ramp", "string"),
    (41, "bridge_highway_segment", "string"),
    (42, "latitude", "double"),
    (43, "longitude", "double"),
    (44, "location_lon", "double"),   # flattened from the `location` GeoJSON point
    (45, "location_lat", "double"),   # flattened from the `location` GeoJSON point
    (46, "computed_region_community_districts", "double"),
    (47, "computed_region_borough_boundaries", "double"),
    (48, "computed_region_police_precincts", "double"),
    (49, "computed_region_city_council_districts", "double"),
    (50, "updated_at", "updated_at"),  # :updated_at, the watermark field
]

# Map bronze column name -> raw Socrata API field name (for parsing raw JSON rows).
API_FIELD_NAME = {
    "location_lon": "location",  # special-cased in parse_row: coordinates[0]
    "location_lat": "location",  # special-cased in parse_row: coordinates[1]
    "computed_region_community_districts": ":@computed_region_f5dn_yrer",
    "computed_region_borough_boundaries": ":@computed_region_yeji_bk3q",
    "computed_region_police_precincts": ":@computed_region_sbqj_enih",
    "computed_region_city_council_districts": ":@computed_region_92fq_4b7q",
    "updated_at": ":updated_at",
}


def _api_name(bronze_name):
    return API_FIELD_NAME.get(bronze_name, bronze_name)


def iceberg_schema():
    fields = []
    for field_id, name, kind in COLUMNS:
        if kind == "string":
            t = StringType()
        elif kind == "timestamp":
            t = TimestampType()
        elif kind == "double":
            t = DoubleType()
        elif kind == "updated_at":
            t = TimestamptzType()
        else:
            raise ValueError(kind)
        fields.append(NestedField(field_id=field_id, name=name, field_type=t, required=(name == "unique_key")))
    return Schema(*fields)


def pyarrow_schema():
    fields = []
    for _, name, kind in COLUMNS:
        if kind == "string":
            t = pa.string()
        elif kind == "timestamp":
            t = pa.timestamp("us")
        elif kind == "double":
            t = pa.float64()
        elif kind == "updated_at":
            t = pa.timestamp("us", tz="UTC")
        else:
            raise ValueError(kind)
        fields.append(pa.field(name, t, nullable=(name != "unique_key")))
    return pa.schema(fields)


def _parse_naive_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s)


def _parse_aware_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_row(raw):
    """Convert one raw Socrata JSON row dict into a dict matching the bronze schema."""
    out = {}
    location = raw.get("location") or {}
    coords = location.get("coordinates") if isinstance(location, dict) else None
    for _, name, kind in COLUMNS:
        api_name = _api_name(name)
        if name == "location_lon":
            out[name] = float(coords[0]) if coords else None
        elif name == "location_lat":
            out[name] = float(coords[1]) if coords else None
        elif kind == "timestamp":
            out[name] = _parse_naive_ts(raw.get(api_name))
        elif kind == "updated_at":
            out[name] = _parse_aware_ts(raw.get(api_name))
        elif kind == "double":
            v = raw.get(api_name)
            out[name] = float(v) if v not in (None, "") else None
        else:
            out[name] = raw.get(api_name)
    return out


def select_clause():
    """Comma-joined list of raw Socrata API field names needed to populate every
    bronze column. Socrata's default (no $select) response silently OMITS
    :updated_at and several other fields — every production request must pass this
    explicitly, or the watermark field comes back null with no error."""
    seen = []
    for _, name, _ in COLUMNS:
        api_name = _api_name(name)
        if api_name not in seen:
            seen.append(api_name)
    return ",".join(seen)


def rows_to_arrow(rows):
    """Convert a list of raw Socrata JSON row dicts into a PyArrow table matching
    the bronze schema."""
    parsed = [parse_row(r) for r in rows]
    columns = {name: [p[name] for p in parsed] for _, name, _ in COLUMNS}
    return pa.table(columns, schema=pyarrow_schema())
