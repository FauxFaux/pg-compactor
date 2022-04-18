### pg-compactor

`pg-compactor` will attempt to shrink bloated tables, like
`vacuum full` but without the 12 hours of downtime.

It has benefits over `pgcompacttable` and `pg-repack` in that it's
stupidly simple. There's no C code, no non-standard extensions, no
multi-second operations of any kind.

In fact, the only SQL it runs which modifies the database is:

```sql
update your_table set id=id where id=?
```

No, really.


### Usage

As a superuser:
```postgresql
create extension pageinspect;
```

In a shell:
```shell
# connection details for the primary, and the replica, if you want a replica
export PG_MAIN='postgresql://me@localhost/foo'
export PG_REPLICA=$PG_MAIN
# the table to pack
export TABLE=my_table
# the field to update, e.g. the `id` column, or some other boring column
export FIELD=modified

python3 ./app.py
```

Then, when it's run for a bit, kill it. Up to you to pick when.

You must kill it, though, it doesn't stop, and will eventually cause
your table to become more bloated. Should probably fix that, somehow.

Then, `vacuum` your table:
```postgresql
vacuum (verbose, analyse) my_table;
```

And see if it worked, then run it again to move more stuff. 


### Discussion

#### Main benefits

 * Uses only an [existing, standard extension](https://www.postgresql.org/docs/9.0/pageinspect.html),
     which is available from 8.3 onwards, and is available on Google CloudSQL.
 * Only runs *obviously* no-op updates, the rest of the work is done by
     existing postgres internals. We're just helping them along.
 * Can be cancelled at any time with no work lost.
 * No explicit locks at all; only the row locks the `UPDATE` takes.
 * Executes only a small number of updates (<<100), affecting
     <100 rows, per transaction (<<~10ms on a modern database).


#### Main disadvantages

 * Bogglingly dumb. Fast enough in practice, but, like, don't think
    too much about just how inefficient this is.
 * Does need (fake)-superuser during the *read* phase, but not for
    the updates. The reads can be run on a replica, and can be inspected;
    they are purely read-only (SELECT against read-only functions).


#### What?

Postgres stores data on disc, in files. Some access patterns result
in these files being much, much bigger than the amount of useful data
in them. You can easily end up with a table containing 200GB of usable
data, but which uses 2TB of disc space.

Postgres will not attempt to heal this discrepancy. If your table was
once 2TB, it'll probably be 2TB again, so just leave it*! It'll be fine.

This doesn't matter... most of the time. Maybe it matters to you, for
example, because your cloud provider is charging you for this space,
or because you're hitting som cases where it does matter (e.g. some
types of seq scans).

(*There's one exception; if it happens that your data is at the start of
the table, and there's just empty space at the end, Postgres will return
that empty space to the operating system. This almost never happens.)


#### How?

Each row in Postgres has a physical storage location, internally called
a `ctid`. Rows with high `ctid`s are near the end of the table.

Postgres doesn't `UPDATE` rows. Postgres `INSERT`s a new row with the new
values in it, then `DELETE`s the old row. This `INSERT` will go to a
new place in the table.

Conceptually, this script does in a loop:

 1) `select max(ctid) from {table}`
 2) `update {table} set {field}={field} where ctid=?`.

Postgres will choose a new location to insert the row, which will not
be at the end of the table; thus leaving free space at the end of the
table, which vacuum will be able to return to the operating system.


#### Easy! Can't I do this by hand?

Guess what I tried first.

Couple of problems, firstly, `select max(ctid) from {table}` doesn't
get optimised, so hits the awful seq-scan case; and will take 30 minutes
on a reasonable multi-terabyte table. This is avoided by using
`pageinspect.get_raw_page` on page numbers, manually.

Secondly, Postgres is aware that `UPDATE`s are going to happen, and
doesn't want to do a lot of work. The primary mechanism here is named
[Postgres Heap Only Tuples](https://git.postgresql.org/gitweb/?p=postgresql.git;a=blob;f=src/backend/access/heap/README.HOT).
PHOTs attempt to prevent us from moving rows by placing the new version
of the row on the same page as the old version. This can be, uh, circumvented
by updating the row over. and. over until the PHOTs run out of space. For
small rows, this can take tens of updates. The script tries up to 100 times,
in a transaction.

Thirdly, a number of the things mentioned here are quite slow, so the script
runs them in batches, instead of one at a time, while still being sensitive
about transaction length.


### License

`MIT OR Apache-2.0` (like used in the Rust ecosystem)
