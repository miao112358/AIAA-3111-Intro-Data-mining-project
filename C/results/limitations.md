# Member C limitations

1. `import_complete=True` confirms file import, not complete capture of all
   real-world Citi Bike trips. AM, MID and PM coverage statuses remain
   `unknown`; results describe imported observations only.
2. Missing sparse OD keys are treated as zero observed rides for imported
   dates under the approved protocol. They are not claims of zero real-world
   demand.
3. The main analysis uses 1 km grid corridors. A straight line between grid
   centers is not a traveled street route, and the analysis does not identify
   individual riders or round trips.
4. Weekday means Monday through Friday. Holidays were not separately marked,
   so weekday results can include public holidays.
5. The full-year Top-10, monthly profile and context comparisons are
   post-selection descriptions. Only the frozen Development-list evaluation
   on October through December is an independent time split.
6. `n_min`, `p_min` and `min_support` are operational pattern definitions, not
   null-hypothesis significance thresholds. B's Development-only sensitivity
   table should accompany the main results.
7. The popular baseline is restricted to the same FullYear candidate pool as
   the tidal analysis. It is not a ranking of every possible NYC grid pair.
8. Weather, service outages, station capacity, rebalancing, user type and
   neighborhood characteristics are not modeled. The results are descriptive
   associations and do not establish causality or bike shortages.
