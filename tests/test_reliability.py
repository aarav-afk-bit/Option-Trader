import threading
import time

from option_trader.providers.base import DataProvider

from .helpers import RecordedNSEProvider, manager_with


class SlowNSE(RecordedNSEProvider):
    def get_option_chain(self, symbol, expiry=None):
        time.sleep(0.2)
        return super().get_option_chain(symbol, expiry)


def test_concurrent_requests_share_one_fetch():
    p = SlowNSE()
    m = manager_with(p)
    results = []
    threads = [threading.Thread(target=lambda: results.append(m.get_chain("NIFTY", use_cache=False)))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 5
    assert p.calls == 1


def test_single_flight_propagates_errors():
    m = manager_with()
    try:
        m._single_flight(("x",), lambda: (_ for _ in ()).throw(ValueError("boom")))
    except ValueError:
        pass
    assert m._inflight == {}


def test_throttle_spaces_requests():
    class Throttled(RecordedNSEProvider):
        min_interval = 0.15

    m = manager_with(Throttled())
    t0 = time.monotonic()
    m.get_chain("NIFTY", use_cache=False)
    m.get_chain("NIFTY", use_cache=False)
    assert time.monotonic() - t0 >= 0.15
    assert DataProvider.min_interval == 0
