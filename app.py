import os

import psycopg2

relname = os.environ['TABLE']
field = os.environ['FIELD']

repl = psycopg2.connect(os.environ['PG_REPLICA'])
main = psycopg2.connect(os.environ['PG_MAIN'])

curr_repl = repl.cursor()
curr_repl.execute(" select relpages from pg_class where relname=%s", (relname,))
rows = curr_repl.fetchall()
if len(rows) != 1:
    raise f'found more than one table named {relname}'
last_page = rows[0][0] - 1

pages_at_a_time = 10_000
page_size = 8 * 1024  # 8192


def human_page(page):
    gb = page * 8 / 1024 / 1024
    return f'{gb:.6f}gb'


print(f'table size on disc: {human_page(last_page)}')

for start in range(last_page - pages_at_a_time, 0, -pages_at_a_time):
    end = start + pages_at_a_time
    # lp_len=0: there's no data here (this slot is unused)
    # t_xmax!=0: this record is deleted in some version, so we can ignore it
    curr_repl.execute("""select * from (select page, (
        select array_agg(lp) from heap_page_items(get_raw_page(%s, page))
             where lp_len!=0 and t_xmax=0
    ) lps from generate_series(%s, %s) page) t where lps is not null;
    """.strip(), (relname, start, end))
    for (page, lps) in curr_repl.fetchall():
        live_ctids = [(page, lp) for lp in lps]
        for attempt in range(100):
            with main.cursor() as curr_main:
                print(f'{human_page(page)}: {len(lps)} rows: {live_ctids}')
                curr_main.execute(f'update {relname} set {field}={field} where ctid in %s', (tuple(str(s) for s in live_ctids),))
                print(f'attempt {attempt} moved {curr_main.rowcount}/{len(lps)} rows')
                # we see rows we can't update; instead of trying again just give up; the script can be run again
                if curr_main.rowcount == 0:
                    break
                curr_main.execute("""select lp from heap_page_items(get_raw_page(%s, %s))
                    where lp_len!=0 and t_xmax=0""", (relname, page))
                lps = curr_main.fetchall()
                if len(lps) == 0:
                    break
                live_ctids = [(page, lp[0]) for lp in lps]
        main.commit()
