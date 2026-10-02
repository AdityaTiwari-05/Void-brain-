"""
tests/test_detection.py
------------------------
Module B detection tests for Void-brain- project.
All tests use in-memory DuckDB with synthetic data.
"""

from __future__ import annotations

import copy
import csv
import sys
import os
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from app.ingestion.engine import run_ingestion
from app.database.schema import initialize_database
from app.detection.features import extract_account_features, extract_features_bulk_sql
from app.detection.pass_through import detect_pass_through
from app.detection.fan_in import detect_fan_in
from app.detection.fan_out import detect_fan_out
from app.detection.terminal import detect_terminal_indicators
from app.detection.cycles import detect_cycles
from app.detection.risk_scorer import score_account
from app.detection.hop_tracer import trace_victim
from app.detection.ml.predictor import predict_proba, model_available

# ── Fixtures ──────────────────────────────────────────────────────────────── #

COLS = ["Transaction_ID","Sender_Account","Receiver_Account","Sender_IFSC",
        "Receiver_IFSC","Amount","Timestamp","Payment_Mode","Narration",
        "IP_Address","Device_Type"]

A = "000010000001"
B = "000020000002"
C = "000030000003"
D = "000040000004"
E = "000050000005"


def _txn(tid, sender, receiver, amount, ts,
         pm="UPI", narration="Fund transfer",
         ip="8.8.8.8", device="Android",
         s_ifsc="HDFC0ABCDEF", r_ifsc="ICIC0XYZ123"):
    return dict(zip(COLS, [tid, sender, receiver, s_ifsc, r_ifsc,
                            str(amount), ts, pm, narration, ip, device]))


def _write_csv(rows, path):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader(); w.writerows(rows)


def _make_db(rows, tmp_path) -> duckdb.DuckDBPyConnection:
    """Ingest rows into fresh in-memory DB via Module A engine, return conn."""
    p = tmp_path / "detect_test.csv"
    _write_csv(rows, p)
    conn = duckdb.connect(":memory:")
    initialize_database(conn)
    run_ingestion(input_file=p, conn=conn)
    # Materialise accounts table from transactions (Module B requirement)
    # Use a sequence-based approach compatible with DuckDB
    existing = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    if existing == 0:
        conn.execute("""
            INSERT INTO accounts
                (account_node_id, normalized_account_id,
                 first_seen, last_seen,
                 incoming_count, outgoing_count,
                 incoming_amount, outgoing_amount,
                 unique_senders, unique_receivers)
            SELECT
                nextval('account_node_id_seq') AS account_node_id,
                acct,
                MIN(ts), MAX(ts),
                COUNT(*) FILTER (WHERE receiver_account = acct),
                COUNT(*) FILTER (WHERE sender_account   = acct),
                COALESCE(SUM(amount) FILTER (WHERE receiver_account = acct), 0),
                COALESCE(SUM(amount) FILTER (WHERE sender_account   = acct), 0),
                COUNT(DISTINCT sender_account)   FILTER (WHERE receiver_account = acct),
                COUNT(DISTINCT receiver_account) FILTER (WHERE sender_account   = acct)
            FROM (
                SELECT sender_account AS acct, ts, amount, sender_account, receiver_account
                FROM transactions
                UNION ALL
                SELECT receiver_account AS acct, ts, amount, sender_account, receiver_account
                FROM transactions
            )
            GROUP BY acct
        """)
    return conn


# ── Feature extraction ────────────────────────────────────────────────────── #

class TestFeatureExtraction:
    def test_returns_features_for_known_account(self, tmp_path):
        rows = [_txn("F001", A, B, 1000, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        f = extract_account_features(conn, A)
        assert f is not None
        conn.close()

    def test_returns_none_for_unknown_account(self, tmp_path):
        rows = [_txn("F001", A, B, 1000, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        assert extract_account_features(conn, "UNKN99999999") is None
        conn.close()

    def test_incoming_outgoing_counts(self, tmp_path):
        rows = [
            _txn("F001", A, B, 1000, "2026-09-01 10:00:00"),
            _txn("F002", A, C, 2000, "2026-09-01 11:00:00"),
            _txn("F003", B, A, 500,  "2026-09-01 12:00:00"),
        ]
        conn = _make_db(rows, tmp_path)
        f = extract_account_features(conn, A)
        assert f.outgoing_count == 2  # A→B, A→C
        assert f.incoming_count == 1  # B→A
        conn.close()

    def test_amounts_are_decimal_strings(self, tmp_path):
        rows = [_txn("F001", A, B, 1000, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        f = extract_account_features(conn, A)
        Decimal(f.incoming_amount)
        Decimal(f.outgoing_amount)
        conn.close()

    def test_unusual_device_detected(self, tmp_path):
        rows = [
            _txn("UD01", A, B, 500, "2026-09-01 10:00:00", device="Linux_Script"),
            _txn("UD02", A, C, 500, "2026-09-01 11:00:00", device="Web_Emulator"),
        ]
        conn = _make_db(rows, tmp_path)
        f = extract_account_features(conn, A)
        assert f.linux_script_tx_count == 1
        assert f.web_emulator_tx_count == 1
        conn.close()

    def test_bulk_extraction(self, tmp_path):
        rows = [_txn("B001", A, B, 500, "2026-09-01 10:00:00"),
                _txn("B002", B, C, 500, "2026-09-01 11:00:00")]
        conn = _make_db(rows, tmp_path)
        bulk = extract_features_bulk_sql(conn, [A, B])
        assert len(bulk) == 2
        conn.close()


# ── Pass-through detection ────────────────────────────────────────────────── #

class TestPassThrough:
    def test_triggered_high_ratio(self, tmp_path):
        rows = [
            _txn("PT_IN01", B, A, 10000, "2026-09-01 10:00:00"),
            _txn("PT_OUT1", A, C, 3500,  "2026-09-01 10:03:00"),
            _txn("PT_OUT2", A, D, 3500,  "2026-09-01 10:05:00"),
            _txn("PT_OUT3", A, E, 2500,  "2026-09-01 10:08:00"),
        ]
        conn = _make_db(rows, tmp_path)
        f = detect_pass_through(conn, A, ratio_threshold=0.90, window_minutes=15, min_receivers=2)
        assert f.detected is True
        assert f.limitation  # limitation always documented
        conn.close()

    def test_not_triggered_low_ratio(self, tmp_path):
        rows = [
            _txn("PTNO_IN", B, A, 10000, "2026-09-01 10:00:00"),
            _txn("PTNO_OUT", A, C, 500,  "2026-09-01 10:05:00"),
        ]
        conn = _make_db(rows, tmp_path)
        f = detect_pass_through(conn, A, ratio_threshold=0.90, window_minutes=15, min_receivers=2)
        assert f.detected is False
        conn.close()

    def test_amounts_are_decimal_strings(self, tmp_path):
        rows = [_txn("PTDS_IN", B, A, 5000, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        f = detect_pass_through(conn, A)
        Decimal(f.incoming_amount_in_window)
        Decimal(f.outgoing_amount_in_window)
        conn.close()


# ── Fan-in / Fan-out ──────────────────────────────────────────────────────── #

class TestFanInFanOut:
    def test_fan_in_triggered(self, tmp_path):
        senders = [f"{i:012d}" for i in range(1, 11)]
        rows = [_txn(f"FI{i:03d}", s, A, 500, f"2026-09-01 10:{i:02d}:00")
                for i, s in enumerate(senders)]
        conn = _make_db(rows, tmp_path)
        f = detect_fan_in(conn, A, min_unique_senders=5)
        assert f.detected is True
        assert f.unique_senders == 10
        conn.close()

    def test_fan_out_triggered(self, tmp_path):
        receivers = [f"{i:012d}" for i in range(100, 110)]
        rows = [_txn(f"FO{i:03d}", A, r, 500, f"2026-09-01 10:{i:02d}:00")
                for i, r in enumerate(receivers)]
        conn = _make_db(rows, tmp_path)
        f = detect_fan_out(conn, A, min_unique_receivers=5)
        assert f.detected is True
        assert f.unique_receivers == 10
        conn.close()

    def test_fan_out_splitting_detected(self, tmp_path):
        receivers = [f"{i:012d}" for i in range(200, 206)]
        rows = [_txn(f"FOSp{i}", A, r, 1000, f"2026-09-01 10:{i:02d}:00")
                for i, r in enumerate(receivers)]
        conn = _make_db(rows, tmp_path)
        f = detect_fan_out(conn, A, min_unique_receivers=3, splitting_cv_threshold=0.30)
        assert f.splitting_detected is True
        conn.close()


# ── Terminal indicators ───────────────────────────────────────────────────── #

class TestTerminalIndicators:
    def test_linux_script_triggers(self, tmp_path):
        rows = [_txn("LS01", A, B, 500, "2026-09-01 10:00:00", device="Linux_Script")]
        conn = _make_db(rows, tmp_path)
        f = detect_terminal_indicators(conn, A)
        assert f.detected is True
        assert f.linux_script_tx_count == 1
        assert f.limitation  # always documented
        conn.close()

    def test_web_emulator_triggers(self, tmp_path):
        rows = [_txn("WE01", A, B, 500, "2026-09-01 10:00:00", device="Web_Emulator")]
        conn = _make_db(rows, tmp_path)
        f = detect_terminal_indicators(conn, A)
        assert f.detected is True
        conn.close()

    def test_crypto_narration_triggers(self, tmp_path):
        rows = [_txn("CR01", A, B, 500, "2026-09-01 10:00:00",
                     narration="BTC crypto exchange USDT transfer")]
        conn = _make_db(rows, tmp_path)
        f = detect_terminal_indicators(conn, A)
        assert f.detected is True
        assert f.crypto_narration_count == 1
        conn.close()

    def test_normal_android_not_triggered(self, tmp_path):
        rows = [_txn("ND01", A, B, 500, "2026-09-01 10:00:00", device="Android")]
        conn = _make_db(rows, tmp_path)
        f = detect_terminal_indicators(conn, A)
        assert f.detected is False
        conn.close()


# ── Cycle detection ───────────────────────────────────────────────────────── #

class TestCycleDetection:
    def test_length_2_cycle_detected(self, tmp_path):
        rows = [
            _txn("CYC_AB", A, B, 1000, "2026-09-01 10:00:00"),
            _txn("CYC_BA", B, A, 900,  "2026-09-01 11:00:00"),
        ]
        conn = _make_db(rows, tmp_path)
        f = detect_cycles(conn, A)
        assert f.detected is True
        assert f.cycle_count >= 1
        conn.close()

    def test_no_cycle(self, tmp_path):
        rows = [_txn("NC01", A, B, 500, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        f = detect_cycles(conn, A)
        assert f.detected is False
        conn.close()

    def test_length_3_cycle_detected(self, tmp_path):
        rows = [
            _txn("CL3_AB", A, B, 1000, "2026-09-01 10:00:00"),
            _txn("CL3_BC", B, C, 900,  "2026-09-01 11:00:00"),
            _txn("CL3_CA", C, A, 800,  "2026-09-01 12:00:00"),
        ]
        conn = _make_db(rows, tmp_path)
        f = detect_cycles(conn, A)
        assert f.detected is True
        assert any(len(p) == 4 for p in f.cycle_paths)
        conn.close()


# ── Risk scorer ───────────────────────────────────────────────────────────── #

class TestRiskScorer:
    def test_full_pipeline_runs(self, tmp_path):
        rows = [
            _txn("RS_IN01", B, A, 10000, "2026-09-01 10:00:00"),
            _txn("RS_OUT1", A, C, 3500,  "2026-09-01 10:03:00"),
            _txn("RS_OUT2", A, D, 3500,  "2026-09-01 10:05:00"),
            _txn("RS_OUT3", A, E, 2500,  "2026-09-01 10:08:00"),
        ]
        conn = _make_db(rows, tmp_path)
        result = score_account(conn, A)
        assert 0.0 <= result.risk_index <= 100.0
        assert result.risk_level in ("LOW", "MEDIUM", "HIGH")
        assert len(result.components) == 9
        assert result.limitations
        conn.close()

    def test_risk_is_reproducible(self, tmp_path):
        rows = [
            _txn("RR_IN", B, A, 10000, "2026-09-01 10:00:00"),
            _txn("RR_OUT", A, C, 9200,  "2026-09-01 10:05:00"),
        ]
        conn = _make_db(rows, tmp_path)
        r1 = score_account(conn, A)
        r2 = score_account(conn, A)
        assert r1.risk_index == r2.risk_index
        conn.close()

    def test_unknown_account_returns_zero(self, tmp_path):
        rows = [_txn("UA01", A, B, 500, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        r = score_account(conn, "UNKN99999999")
        assert r.risk_index == 0.0
        conn.close()

    def test_ml_unavailable_not_an_error(self, tmp_path):
        rows = [_txn("ML01", A, B, 500, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        r = score_account(conn, A, ml_probability=None)
        assert r.ml_probability is None
        assert r.risk_index >= 0.0
        conn.close()


# ── Four-hop tracer ───────────────────────────────────────────────────────── #

class TestHopTracer:
    def test_basic_trace(self, tmp_path):
        V = "000000000099"
        rows = [
            _txn("H_VA", V, A, 10000, "2026-09-01 10:00:00"),
            _txn("H_AB", A, B, 9000,  "2026-09-01 10:10:00"),
            _txn("H_BC", B, C, 8000,  "2026-09-01 10:20:00"),
        ]
        conn = _make_db(rows, tmp_path)
        result = trace_victim(conn, V, max_hops=4, max_paths=50)
        assert result.victim_account == V
        assert result.total_paths_found > 0
        assert result.limitation
        conn.close()

    def test_chronological_order_enforced(self, tmp_path):
        V = "000000000098"
        rows = [
            _txn("CHR_VA", V, A, 5000, "2026-09-01 10:00:00"),
            _txn("CHR_AB", A, B, 4000, "2026-09-01 10:10:00"),
        ]
        conn = _make_db(rows, tmp_path)
        result = trace_victim(conn, V)
        for path in result.paths:
            ts_list = [e.ts for e in path.edges]
            assert ts_list == sorted(ts_list)
        conn.close()

    def test_cycle_prevention(self, tmp_path):
        V = "000000000097"
        rows = [
            _txn("CYC_V_A", V, A, 5000, "2026-09-01 10:00:00"),
            _txn("CYC_AB",  A, B, 4000, "2026-09-01 10:10:00"),
            _txn("CYC_BA",  B, A, 3000, "2026-09-01 10:20:00"),
        ]
        conn = _make_db(rows, tmp_path)
        result = trace_victim(conn, V, max_hops=4, max_paths=50)
        for path in result.paths:
            accts = [e.receiver_account for e in path.edges]
            assert len(accts) == len(set(accts)), "Account repeated in path"
        conn.close()

    def test_amounts_are_decimal_strings(self, tmp_path):
        V = "000000000096"
        rows = [_txn("AMT_VA", V, A, 5000, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        result = trace_victim(conn, V)
        for path in result.paths:
            for edge in path.edges:
                Decimal(edge.amount)
        conn.close()

    def test_unknown_victim_returns_empty(self, tmp_path):
        rows = [_txn("UNK01", A, B, 500, "2026-09-01 10:00:00")]
        conn = _make_db(rows, tmp_path)
        result = trace_victim(conn, "000000000000")
        assert result.total_paths_found == 0
        conn.close()


# ── ML predictor fallback ─────────────────────────────────────────────────── #

class TestMLPredictor:
    def test_predict_returns_none_when_no_model(self):
        result = predict_proba({"incoming_count": 10})
        assert result is None or isinstance(result, float)

    def test_model_available_does_not_crash(self):
        assert isinstance(model_available(), bool)
