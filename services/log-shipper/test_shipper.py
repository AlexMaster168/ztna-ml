import os

import pytest

import shipper


@pytest.fixture
def logfile(tmp_path):
    return tmp_path / "access.json"


def test_reads_complete_lines_and_buffers_partial(logfile):
    logfile.write_bytes(b'{"a":1}\n{"a":2}\n{"a":')
    t = shipper.Tailer(logfile)
    assert t.open(0, 0)
    assert t.read_lines(10) == [b'{"a":1}', b'{"a":2}']
    # Partial line is not counted as shipped.
    assert t.offset == len(b'{"a":1}\n{"a":2}\n')
    with logfile.open("ab") as f:
        f.write(b"3}\n")
    assert t.read_lines(10) == [b'{"a":3}']
    assert t.offset == logfile.stat().st_size


def test_resumes_from_checkpoint_on_same_inode(logfile):
    logfile.write_bytes(b"one\ntwo\n")
    inode = os.stat(logfile).st_ino
    t = shipper.Tailer(logfile)
    t.open(inode, 4)
    assert t.read_lines(10) == [b"two"]


def test_restarts_from_zero_when_truncated(logfile):
    logfile.write_bytes(b"x\n")
    t = shipper.Tailer(logfile)
    t.open(os.stat(logfile).st_ino, 999)
    assert t.read_lines(10) == [b"x"]


def test_batch_limit(logfile):
    logfile.write_bytes(b"".join(f"{i}\n".encode() for i in range(10)))
    t = shipper.Tailer(logfile)
    t.open(0, 0)
    assert len(t.read_lines(3)) == 3
    assert len(t.read_lines(100)) == 7


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(shipper, "STATE_PATH", tmp_path / "offset.json")
    assert shipper.load_state() == (0, 0)
    shipper.save_state(42, 1000)
    assert shipper.load_state() == (42, 1000)


def test_shard_routing_matches_ml_package():
    import zlib

    # Reference values computed with ztna_ml.shards.shard_of (crc32 of "gw|ip" mod n).
    for gw, ip in [("gw-1", "10.13.0.2"), ("gw-2", "10.14.0.3")]:
        assert shipper.shard_of(gw, ip, 8) == zlib.crc32(f"{gw}|{ip}".encode()) % 8
    line = b'{"peer_ip":"10.13.0.2","status":200}'
    assert shipper.stream_for(line) == f"traffic:http:{shipper.shard_of('gw-1', '10.13.0.2')}"
    assert shipper.stream_for(b"garbage").startswith("traffic:http:")
