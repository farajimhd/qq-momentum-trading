"""ClickHouse bulk equivalent of resistance selection over certified v4 closes."""
import re


def selection_sql(database):
    if not re.fullmatch(r'structure_book_[a-f0-9]{12}', database):
        raise ValueError('Invalid source book')
    return f"""
WITH source AS (
 SELECT ticker,valid_from_us,level_id,price,lower,upper,state_json,
 JSONExtractString(state_json,'state') AS state,
 JSONExtractString(state_json,'side') AS role,
 JSONExtractFloat(state_json,'history_threshold') AS threshold,
 least(1.,if(threshold>0,JSONExtractFloat(state_json,'best_departure')/threshold,0.)) AS departure,
 JSONExtractUInt(state_json,'independent_retests') AS tests,
 JSONExtractUInt(state_json,'role_retests') AS role_tests,
 JSONExtractUInt(state_json,'accepted_crossings') AS crossings,
 JSONExtractRaw(state_json,'last_role_change_at') NOT IN ('','null') AS flipped,
 greatest(0.,40*departure+40*least(if(flipped,role_tests,tests)/3.,1.)+20*least(role_tests/2.,1.)-20*crossings) AS raw_score,
 if(flipped AND role_tests=0,least(raw_score,20.),raw_score) AS score
 FROM {database}.book FINAL
 WHERE scale='major' AND state='active' AND role='resistance'
 AND greatest(JSONExtractFloat(state_json,'pivot_at'),JSONExtractFloat(state_json,'confirmed_at'),
 JSONExtractFloat(state_json,'formed_at'),JSONExtractFloat(state_json,'last_role_change_at'))<=valid_from_us/1000000.
), ordered AS (
 SELECT ticker,valid_from_us,arraySort(x->(x.1,x.4),groupArray((lower,upper,price,level_id,score,state_json))) AS a
 FROM source GROUP BY ticker,valid_from_us
), grouped AS (
 SELECT *, arrayFold((acc,x)->
 if(acc.3=0 OR (greatest(x.2,acc.2)-acc.1)/acc.1*10000>100,
 (x.1,x.2,acc.3+1,arrayPushBack(acc.4,acc.3+1)),
 (acc.1,greatest(acc.2,x.2),acc.3,arrayPushBack(acc.4,acc.3))),
 a,(toFloat64(0),toFloat64(0),toUInt64(0),CAST([],'Array(UInt64)'))).4 AS groups FROM ordered
), flat AS (
 SELECT ticker,valid_from_us,pair.1 AS r,pair.2 AS g FROM grouped ARRAY JOIN arrayZip(a,groups) AS pair
)
SELECT ticker,valid_from_us,
 concat('r:',substring(lower(hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(r.4))), '|')))),1,16)) AS level_id,
 min(r.1) AS lower,max(r.2) AS upper,
 argMax(r.3,(r.5,r.3,-r.1,-toInt64(r.4))) AS price,
 round(max(r.5),1) AS selection_score,
 arraySort(groupArray(toString(r.4))) AS members
FROM flat GROUP BY ticker,valid_from_us,g HAVING max(r.5)>=30
"""
