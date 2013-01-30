"""Tests for tasks.py."""

import time
import unittest

from . import events
from . import futures
from . import tasks
from . import test_utils


class Dummy:
    def __repr__(self):
        return 'Dummy()'
    def __call__(self, *args):
        pass


class TaskTests(test_utils.LogTrackingTestCase):

    def setUp(self):
        super().setUp()
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()
        super().tearDown()

    def test_task_class(self):
        @tasks.coroutine
        def notmuch():
            yield from []
            return 'ok'
        t = tasks.Task(notmuch())
        self.event_loop.run()
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')

    def test_task_decorator(self):
        @tasks.task
        def notmuch():
            yield from []
            return 'ko'
        t = notmuch()
        self.event_loop.run()
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ko')

    def test_task_repr(self):
        @tasks.task
        def notmuch():
            yield from []
            return 'abc'
        t = notmuch()
        t.add_done_callback(Dummy())
        self.assertEqual(repr(t), 'Task(<notmuch>)<PENDING, [Dummy()]>')
        t.cancel()  # Does not take immediate effect!
        self.assertEqual(repr(t), 'Task(<notmuch>)<CANCELLING, [Dummy()]>')
        self.assertRaises(futures.CancelledError,
                          self.event_loop.run_until_complete, t)
        self.assertEqual(repr(t), 'Task(<notmuch>)<CANCELLED>')
        t = notmuch()
        self.event_loop.run_until_complete(t)
        self.assertEqual(repr(t), "Task(<notmuch>)<result='abc'>")

    def test_task_basics(self):
        @tasks.task
        def outer():
            a = yield from inner1()
            b = yield from inner2()
            return a+b
        @tasks.task
        def inner1():
            yield from []
            return 42
        @tasks.task
        def inner2():
            yield from []
            return 1000
        t = outer()
        self.assertEqual(self.event_loop.run_until_complete(t), 1042)

    def test_wait(self):
        a = tasks.sleep(0.1)
        b = tasks.sleep(0.15)
        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a])
            self.assertEqual(done, set([a, b]))
            self.assertEqual(pending, set())
            return 42
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        self.assertEqual(res, 42)
        # Doing it again should take no time and exercise a different path.
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)
        # TODO: Test different return_when values.

    def test_wait_with_exception(self):
        self.suppress_log_errors()
        a = tasks.sleep(0.1)
        @tasks.coroutine
        def sleeper():
            yield from tasks.sleep(0.15)
            raise ZeroDivisionError('really')
        b = tasks.Task(sleeper())
        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a])
            self.assertEqual(len(done), 2)
            self.assertEqual(pending, set())
            errors = set(f for f in done if f.exception() is not None)
            self.assertEqual(len(errors), 1)
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)

    def test_wait_with_timeout(self):
        a = tasks.sleep(0.1)
        b = tasks.sleep(0.15)
        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a], timeout=0.11)
            self.assertEqual(done, set([a]))
            self.assertEqual(pending, set([b]))
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.1)
        self.assertTrue(t1-t0 <= 0.13)

    def test_as_completed(self):
        @tasks.coroutine
        def sleeper(dt, x):
            yield from tasks.sleep(dt)
            return x
        a = sleeper(0.1, 'a')
        b = sleeper(0.1, 'b')
        c = sleeper(0.15, 'c')
        @tasks.coroutine
        def foo():
            values = []
            for f in tasks.as_completed([b, c, a]):
                values.append((yield from f))
            return values
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        self.assertTrue('a' in res[:2])
        self.assertTrue('b' in res[:2])
        self.assertEqual(res[2], 'c')
        # Doing it again should take no time and exercise a different path.
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)

    def test_as_completed_with_timeout(self):
        self.suppress_log_errors()
        a = tasks.sleep(0.1, 'a')
        b = tasks.sleep(0.15, 'b')
        @tasks.coroutine
        def foo():
            values = []
            for f in tasks.as_completed([a, b], timeout=0.12):
                try:
                    v = yield from f
                    values.append((1, v))
                except futures.TimeoutError as exc:
                    values.append((2, exc))
            return values
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.11)
        self.assertEqual(len(res), 2, res)
        self.assertEqual(res[0], (1, 'a'))
        self.assertEqual(res[1][0], 2)
        self.assertTrue(isinstance(res[1][1], futures.TimeoutError))

    def test_sleep(self):
        @tasks.coroutine
        def sleeper(dt, arg):
            yield from tasks.sleep(dt/2)
            res = yield from tasks.sleep(dt/2, arg)
            return res
        t = tasks.Task(sleeper(0.1, 'yeah'))
        t0 = time.monotonic()
        self.event_loop.run()
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.09)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'yeah')

    def test_task_cancel_sleeping_task(self):
        sleepfut = None
        @tasks.task
        def sleep(dt):
            nonlocal sleepfut
            sleepfut = tasks.sleep(dt)
            try:
                t0 = time.monotonic()
                yield from sleepfut
            finally:
                t1 = time.monotonic()
        @tasks.task
        def doit():
            sleeper = sleep(5000)
            self.event_loop.call_later(0.1, sleeper.cancel)
            try:
                t0 = time.monotonic()
                yield from sleeper
            except futures.CancelledError:
                t1 = time.monotonic()
                return 'cancelled'
            else:
                return 'slept in'
        t0 = time.monotonic()
        doer = doit()
        self.assertEqual(self.event_loop.run_until_complete(doer), 'cancelled')
        t1 = time.monotonic()
        self.assertTrue(0.09 <= t1-t0 <= 0.13, (t1-t0, sleepfut, doer))


if __name__ == '__main__':
    unittest.main()