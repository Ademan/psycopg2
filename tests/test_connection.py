#!/usr/bin/env python

import unittest
from operator import attrgetter

import psycopg2
import psycopg2.extensions
import tests

class ConnectionTests(unittest.TestCase):

    def connect(self):
        return psycopg2.connect(tests.dsn)

    def test_closed_attribute(self):
        conn = self.connect()
        self.assertEqual(conn.closed, False)
        conn.close()
        self.assertEqual(conn.closed, True)

    def test_cursor_closed_attribute(self):
        conn = self.connect()
        curs = conn.cursor()
        self.assertEqual(curs.closed, False)
        curs.close()
        self.assertEqual(curs.closed, True)

        # Closing the connection closes the cursor:
        curs = conn.cursor()
        conn.close()
        self.assertEqual(curs.closed, True)

    def test_reset(self):
        conn = self.connect()
        # switch isolation level, then reset
        level = conn.isolation_level
        conn.set_isolation_level(0)
        self.assertEqual(conn.isolation_level, 0)
        conn.reset()
        # now the isolation level should be equal to saved one
        self.assertEqual(conn.isolation_level, level)

    def test_notices(self):
        conn = self.connect()
        cur = conn.cursor()
        cur.execute("create temp table chatty (id serial primary key);")
        self.assertEqual("CREATE TABLE", cur.statusmessage)
        self.assert_(conn.notices)
        conn.close()

    def test_server_version(self):
        conn = self.connect()
        self.assert_(conn.server_version)

    def test_protocol_version(self):
        conn = self.connect()
        self.assert_(conn.protocol_version in (2,3), conn.protocol_version)

    def test_isolation_level(self):
        conn = self.connect()
        self.assertEqual(
            conn.isolation_level,
            psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)

    def test_encoding(self):
        conn = self.connect()
        self.assert_(conn.encoding in psycopg2.extensions.encodings)


def skip_if_tpc_disabled(f):
    """Skip a test if the server has tpc support disabled."""
    def skip_if_tpc_disabled_(self):
        cnn = self.connect()
        cur = cnn.cursor()
        try:
            cur.execute("SHOW max_prepared_transactions;")
        except psycopg2.ProgrammingError:
            # Server version too old: let's die a different death
            mtp = 1
        else:
            mtp = int(cur.fetchone()[0])
        cnn.close()

        if not mtp:
            import warnings
            warnings.warn(
                "server not configured for two phase transactions. "
                "set max_prepared_transactions to > 0 to run the test")
            return
        return f(self)

    skip_if_tpc_disabled_.__name__ = f.__name__
    return skip_if_tpc_disabled_

class ConnectionTwoPhaseTests(unittest.TestCase):
    def setUp(self):
        self.make_test_table()
        self.clear_test_xacts()

    def tearDown(self):
        self.clear_test_xacts()

    def clear_test_xacts(self):
        """Rollback all the prepared transaction in the testing db."""
        cnn = self.connect()
        cnn.set_isolation_level(0)
        cur = cnn.cursor()
        cur.execute(
            "select gid from pg_prepared_xacts where database = %s",
            (tests.dbname,))
        gids = [ r[0] for r in cur ]
        for gid in gids:
            cur.execute("rollback prepared %s;", (gid,))
        cnn.close()

    def make_test_table(self):
        cnn = self.connect()
        cur = cnn.cursor()
        cur.execute("DROP TABLE IF EXISTS test_tpc;")
        cur.execute("CREATE TABLE test_tpc (data text);")
        cnn.commit()
        cnn.close()

    def count_xacts(self):
        """Return the number of prepared xacts currently in the test db."""
        cnn = self.connect()
        cur = cnn.cursor()
        cur.execute("""
            select count(*) from pg_prepared_xacts
            where database = %s;""",
            (tests.dbname,))
        rv = cur.fetchone()[0]
        cnn.close()
        return rv

    def count_test_records(self):
        """Return the number of records in the test table."""
        cnn = self.connect()
        cur = cnn.cursor()
        cur.execute("select count(*) from test_tpc;")
        rv = cur.fetchone()[0]
        cnn.close()
        return rv

    def connect(self):
        return psycopg2.connect(tests.dsn)

    @skip_if_tpc_disabled
    def test_tpc_commit(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_PREPARED)
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_commit()
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(1, self.count_test_records())

    def test_tpc_commit_one_phase(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit_1p');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_commit()
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(1, self.count_test_records())

    @skip_if_tpc_disabled
    def test_tpc_commit_recovered(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit_rec');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        cnn.close()
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        cnn.tpc_commit(xid)

        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(1, self.count_test_records())

    @skip_if_tpc_disabled
    def test_tpc_rollback(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_rollback');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_PREPARED)
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_rollback()
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

    def test_tpc_rollback_one_phase(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_rollback_1p');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_rollback()
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

    @skip_if_tpc_disabled
    def test_tpc_rollback_recovered(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit_rec');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        cnn.close()
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        cnn.tpc_rollback(xid)

        self.assertEqual(cnn.status, psycopg2.extensions.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

    def test_status_after_recover(self):
        cnn = self.connect()
        self.assertEqual(psycopg2.extensions.STATUS_READY, cnn.status)
        xns = cnn.tpc_recover()
        self.assertEqual(psycopg2.extensions.STATUS_READY, cnn.status)

        cur = cnn.cursor()
        cur.execute("select 1")
        self.assertEqual(psycopg2.extensions.STATUS_BEGIN, cnn.status)
        xns = cnn.tpc_recover()
        self.assertEqual(psycopg2.extensions.STATUS_BEGIN, cnn.status)

    @skip_if_tpc_disabled
    def test_recovered_xids(self):
        # insert a few test xns
        cnn = self.connect()
        cnn.set_isolation_level(0)
        cur = cnn.cursor()
        cur.execute("begin; prepare transaction '1-foo';")
        cur.execute("begin; prepare transaction '2-bar';")

        # read the values to return
        cur.execute("""
            select gid, prepared, owner, database
            from pg_prepared_xacts
            where database = %s;""",
            (tests.dbname,))
        okvals = cur.fetchall()
        okvals.sort()

        cnn = self.connect()
        xids = cnn.tpc_recover()
        xids = [ xid for xid in xids if xid.database == tests.dbname ]
        xids.sort(key=attrgetter('gtrid'))

        # check the values returned
        self.assertEqual(len(okvals), len(xids))
        for (xid, (gid, prepared, owner, database)) in zip (xids, okvals):
            self.assertEqual(xid.gtrid, gid)
            self.assertEqual(xid.prepared, prepared)
            self.assertEqual(xid.owner, owner)
            self.assertEqual(xid.database, database)

    @skip_if_tpc_disabled
    def test_xid_encoding(self):
        cnn = self.connect()
        xid = cnn.xid(42, "gtrid", "bqual")
        cnn.tpc_begin(xid)
        cnn.tpc_prepare()

        cnn = self.connect()
        cur = cnn.cursor()
        cur.execute("select gid from pg_prepared_xacts where database = %s;",
            (tests.dbname,))
        self.assertEqual('42_Z3RyaWQ=_YnF1YWw=', cur.fetchone()[0])

    @skip_if_tpc_disabled
    def test_xid_roundtrip(self):
        for fid, gtrid, bqual in [
            (0, "", ""),
            (42, "gtrid", "bqual"),
            (0x7fffffff, "x" * 64, "y" * 64),
        ]:
            cnn = self.connect()
            xid = cnn.xid(fid, gtrid, bqual)
            cnn.tpc_begin(xid)
            cnn.tpc_prepare()
            cnn.close()

            cnn = self.connect()
            xids = [ xid for xid in cnn.tpc_recover()
                if xid.database == tests.dbname ]
            self.assertEqual(1, len(xids))
            xid = xids[0]
            self.assertEqual(xid.format_id, fid)
            self.assertEqual(xid.gtrid, gtrid)
            self.assertEqual(xid.bqual, bqual)

            cnn.tpc_rollback(xid)

    @skip_if_tpc_disabled
    def test_unparsed_roundtrip(self):
        for tid in [
            '',
            'hello, world!',
            'x' * 199,  # PostgreSQL's limit in transaction id length
        ]:
            cnn = self.connect()
            cnn.tpc_begin(tid)
            cnn.tpc_prepare()
            cnn.close()

            cnn = self.connect()
            xids = [ xid for xid in cnn.tpc_recover()
                if xid.database == tests.dbname ]
            self.assertEqual(1, len(xids))
            xid = xids[0]
            self.assertEqual(xid.format_id, None)
            self.assertEqual(xid.gtrid, tid)
            self.assertEqual(xid.bqual, None)

            cnn.tpc_rollback(xid)

    def test_xid_construction(self):
        from psycopg2.extensions import Xid

        x1 = Xid(74, 'foo', 'bar')
        self.assertEqual(74, x1.format_id)
        self.assertEqual('foo', x1.gtrid)
        self.assertEqual('bar', x1.bqual)

    def test_xid_from_string(self):
        from psycopg2.extensions import Xid

        x2 = Xid.from_string('42_Z3RyaWQ=_YnF1YWw=')
        self.assertEqual(42, x2.format_id)
        self.assertEqual('gtrid', x2.gtrid)
        self.assertEqual('bqual', x2.bqual)

        x3 = Xid.from_string('99_xxx_yyy')
        self.assertEqual(None, x3.format_id)
        self.assertEqual('99_xxx_yyy', x3.gtrid)
        self.assertEqual(None, x3.bqual)

    def test_xid_to_string(self):
        from psycopg2.extensions import Xid

        x1 = Xid.from_string('42_Z3RyaWQ=_YnF1YWw=')
        self.assertEqual(str(x1), '42_Z3RyaWQ=_YnF1YWw=')

        x2 = Xid.from_string('99_xxx_yyy')
        self.assertEqual(str(x2), '99_xxx_yyy')

    @skip_if_tpc_disabled
    def test_xid_unicode(self):
        cnn = self.connect()
        x1 = cnn.xid(10, u'uni', u'code')
        cnn.tpc_begin(x1)
        cnn.tpc_prepare()
        cnn.reset()
        xid = [ xid for xid in cnn.tpc_recover()
            if xid.database == tests.dbname ][0]
        self.assertEqual(10, xid.format_id)
        self.assertEqual('uni', xid.gtrid)
        self.assertEqual('code', xid.bqual)

    @skip_if_tpc_disabled
    def test_xid_unicode_unparsed(self):
        # We don't expect people shooting snowmen as transaction ids,
        # so if something explodes in an encode error I don't mind.
        # Let's just check uniconde is accepted as type.
        cnn = self.connect()
        cnn.set_client_encoding('utf8')
        cnn.tpc_begin(u"transaction-id")
        cnn.tpc_prepare()
        cnn.reset()

        xid = [ xid for xid in cnn.tpc_recover()
            if xid.database == tests.dbname ][0]
        self.assertEqual(None, xid.format_id)
        self.assertEqual('transaction-id', xid.gtrid)
        self.assertEqual(None, xid.bqual)


def test_suite():
    return unittest.TestLoader().loadTestsFromName(__name__)

if __name__ == "__main__":
    unittest.main()
