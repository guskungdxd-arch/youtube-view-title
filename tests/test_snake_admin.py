#!/usr/bin/env python3
"""เทสต์ส่วนที่แอดมินคุมเกมงู — ตั้งความเร็ว, ลบประวัติ, และรายชื่อคนที่มาเล่น

รันด้วย:  ./web/venv/bin/python tests/test_snake_admin.py

สำคัญ: ตั้ง RUN_SCHEDULER=0 + ชี้ DATABASE_PATH/QUOTA_STATE_PATH ไปที่ temp
ก่อน import app เสมอ ไม่งั้น import เฉย ๆ จะสตาร์ท scheduler จริงและไปแก้ชื่อ
คลิปจริงของเจ้าของ เทสต์นี้ไม่ยิงเน็ตเลย
"""
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
import traceback

_TMP = tempfile.mkdtemp(prefix="viewtitle-snake-tests-")
os.environ["RUN_SCHEDULER"] = "0"
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "snake.db")
os.environ["QUOTA_STATE_PATH"] = os.path.join(_TMP, "snake-quota.json")
os.environ.setdefault("FLASK_SECRET", "test-secret")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.pop("ADMIN_EMAILS", None)      # ให้ fallback "คนแรกคือแอดมิน" ทำงาน

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
import app  # noqa: E402

SECRET = "credentials-must-never-leave-the-server"

def build_run(seed):
    """สร้างการเล่นที่ "จบจริง" จาก seed ที่เซิร์ฟเวอร์แจกมา คืน (inputs, steps, score)

    งูยาว 4 ช่องชนตัวเองไม่ได้เลย: วงที่สั้นที่สุดบนตารางคือ 4 ก้าว ซึ่งพอดีกับ
    จังหวะที่หางออกจากช่องนั้นแล้ว และกระดานไม่มีขอบ (ทะลุไปโผล่อีกฝั่ง) เกมจึงจบ
    ไม่ได้เลยถ้ายังไม่ได้กินอาหาร ทุกการเล่นที่ส่งขึ้นมาได้จริงจึงมีแต้มเสมอ

    เดินสุ่มแบบมี seed ของตัวเอง แล้วให้ snake_replay เป็นคนบอกว่าตายที่ก้าวไหน
    ลอง 50 ชุดค่อยยอมแพ้ — สุ่มพลาดกันได้ แต่ 50 ครั้งติดแปลว่ามีอย่างอื่นผิด
    """
    for attempt in range(50):
        rng = random.Random(seed * 1000 + attempt)
        inputs = [
            [s, rng.choice([37, 38, 39, 40])]
            for s in range(600)
            if rng.random() < 0.25
        ]
        score, ended = app.snake_replay(seed, [tuple(i) for i in inputs], 900)
        if ended and score > 0:
            return inputs, ended, score
    raise AssertionError(f"สร้างการเล่นตัวอย่างจาก seed {seed} ไม่สำเร็จ")


def seed():
    """คนแรก = เจ้าของ/แอดมิน, สองคนหลัง = ผู้เล่นธรรมดา"""
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM snake_scores")
        conn.execute("DELETE FROM snake_runs")
        conn.execute("DELETE FROM settings")
        for sub, email in [
            ("owner-sub", "owner@example.com"),
            ("player-sub", "player@example.com"),
            ("quiet-sub", "quiet@example.com"),
        ]:
            conn.execute(
                "INSERT INTO users (sub, email, credentials, title_template)"
                " VALUES (?,?,?,?)",
                (sub, email, SECRET, "{views} views"),
            )


def client_as(sub=None):
    c = app.app.test_client()
    if sub:
        with c.session_transaction() as s:
            s["sub"] = sub
    return c


def snapshot(c=None):
    c = c or client_as("owner-sub")
    return json.loads(c.get("/admin/data").get_data(as_text=True))


def backdate(game_id, seconds):
    """ถอยเวลาที่แจก seed ให้ดูเหมือนผู้เล่นใช้เวลาเล่นจริง

    เทสต์ส่ง seed แล้วส่งผลกลับในเสี้ยววินาที ซึ่งของจริงเป็นไปไม่ได้และจะโดน
    ตัวกันโกงตีตกทุกครั้ง ต้องเลื่อน issued_at เอง
    """
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.execute(
            "UPDATE snake_runs SET issued_at = ? WHERE game_id = ?",
            (time.time() - seconds, game_id),
        )


def play(sub, elapsed=None):
    """เล่นหนึ่งเกมให้จบแบบที่เซิร์ฟเวอร์ตรวจผ่าน คืน response ของการส่งคะแนน"""
    c = client_as(sub)
    started = c.post("/api/snake/start").get_json()
    inputs, steps, _ = build_run(started["seed"])
    if elapsed is None:
        # เผื่อไว้เกินขั้นต่ำนิดหน่อย ให้ผ่านตัวกันโกงแบบไม่ก้ำกึ่ง
        elapsed = steps * app.snake_min_ms_per_step(app.snake_frames_per_step()) / 1000 + 0.5
    backdate(started["game_id"], elapsed)
    return c.post(
        "/api/snake/scores",
        json={"game_id": started["game_id"], "steps": steps, "inputs": inputs},
    )


# --------------------------------------------------------------- สิทธิ์เข้าถึง
def test_anonymous_cannot_touch_any_snake_admin_route():
    c = client_as()
    assert c.post("/admin/snake/speed", json={"frames": 8}).status_code == 403
    assert c.post("/admin/snake/delete", json={"id": 1}).status_code == 403
    assert c.post("/admin/snake/clear", json={"confirm": True}).status_code == 403


def test_ordinary_user_cannot_touch_any_snake_admin_route():
    c = client_as("player-sub")
    assert c.post("/admin/snake/speed", json={"frames": 8}).status_code == 403
    assert c.post("/admin/snake/delete", json={"id": 1}).status_code == 403
    assert c.post("/admin/snake/clear", json={"confirm": True}).status_code == 403


def test_ordinary_user_cannot_change_speed_by_calling_it():
    """เช็กผลจริง ไม่ใช่แค่ status code — 403 ต้องแปลว่าไม่มีอะไรถูกเขียน"""
    before = app.snake_frames_per_step()
    client_as("player-sub").post("/admin/snake/speed", json={"frames": 11})
    assert app.snake_frames_per_step() == before


# ------------------------------------------------------------------ ความเร็ว
def test_speed_starts_at_the_original_six():
    """ค่าตั้งต้นต้องเท่าเดิม ไม่งั้นคะแนนเก่ากับใหม่เทียบกันไม่ได้"""
    assert app.snake_frames_per_step() == 6


def test_admin_can_change_speed_and_snapshot_shows_it():
    r = client_as("owner-sub").post("/admin/snake/speed", json={"frames": 9})
    assert r.status_code == 200, r.status_code
    body = r.get_json()
    assert body["snake"]["speed"] == 9, body["snake"]
    assert body["snake"]["speed_label"] == "Relaxed", body["snake"]
    assert app.snake_frames_per_step() == 9


def test_speed_survives_into_the_game_page():
    """ตัวเลขต้องไปโผล่ในโค้ดเกมจริง ไม่ใช่เก็บไว้เฉย ๆ ในฐานข้อมูล"""
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 4})
    page = client_as().get("/snake").get_data(as_text=True)
    assert "if (++count < 4)" in page, "หน้าเกมยังเดินด้วยความเร็วเดิม"
    assert "Fast" in page, "หน้าเกมไม่ได้บอกผู้เล่นว่าความเร็วเปลี่ยนไป"


def test_speed_out_of_range_is_refused():
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 6})
    c = client_as("owner-sub")
    for bad in (0, 1, 13, 100, -5):
        assert c.post("/admin/snake/speed", json={"frames": bad}).status_code == 400, bad
    assert app.snake_frames_per_step() == 6, "ค่านอกช่วงเล็ดลอดเข้าไปได้"


def test_speed_must_be_a_plain_integer():
    c = client_as("owner-sub")
    for bad in ("8", 8.5, True, None, [8]):
        assert c.post("/admin/snake/speed", json={"frames": bad}).status_code == 400, bad


def test_a_broken_value_in_the_database_falls_back_to_default():
    """ค่าเสียใน settings ต้องไม่ทำให้หน้าเกมพัง"""
    app.set_setting(app.SNAKE_SPEED_KEY, "ไม่ใช่ตัวเลข")
    assert app.snake_frames_per_step() == app.SNAKE_SPEED_DEFAULT
    assert client_as().get("/snake").status_code == 200
    app.set_setting(app.SNAKE_SPEED_KEY, 6)


# ------------------------------------------------- ความเร็วกับตัวกันโกงต้องไปด้วยกัน
def test_the_cheat_check_still_matches_the_old_number_at_the_old_speed():
    """ที่ 6 เฟรมต้องได้ 20ms เท่าค่าที่ hard-code ไว้เดิมเป๊ะ — ไม่ใช่แค่ใกล้เคียง"""
    assert app.snake_min_ms_per_step(6) == 20


def test_the_cheat_check_follows_the_speed():
    assert app.snake_min_ms_per_step(12) > app.snake_min_ms_per_step(6)
    assert app.snake_min_ms_per_step(2) < app.snake_min_ms_per_step(6)


def test_a_fast_game_played_at_its_real_pace_is_not_called_cheating():
    """เกมที่ถูกเร่งให้เร็วขึ้น คนเล่นจบเร็วตามเกม ต้องไม่โดนหาว่าโกง

    ถ้าเทสต์นี้แดง แปลว่าเพดานความเร็วถูกตรึงไว้ที่ค่าของเกมช้า แล้วคนเล่นจริง
    จะถูกปฏิเสธคะแนนทั้งที่เล่นถูกต้อง
    """
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 2})
    r = play("player-sub")
    assert r.status_code == 200, r.get_json()
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 6})


def test_a_run_is_judged_by_the_speed_it_was_handed_out_at():
    """แอดมินเปลี่ยนความเร็วกลางคัน เกมที่เล่นค้างอยู่ต้องยังถูกตรวจด้วยค่าเดิม"""
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 2})
    c = client_as("player-sub")
    started = c.post("/api/snake/start").get_json()
    inputs, steps, _ = build_run(started["seed"])
    # ผู้เล่นใช้เวลาพอดีกับเกมความเร็ว 2 แต่ยังไม่ทันส่ง แอดมินก็หน่วงเกมเป็น 12
    backdate(started["game_id"], steps * app.snake_min_ms_per_step(2) / 1000 + 0.5)
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 12})

    r = c.post("/api/snake/scores", json={
        "game_id": started["game_id"], "steps": steps, "inputs": inputs,
    })
    assert r.status_code == 200, r.get_json()
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 6})


def test_a_run_sent_faster_than_the_game_can_run_is_still_refused():
    """ของเดิมต้องไม่หลุด — เร็วเกินจริงยังต้องโดนตีตกเหมือนเดิม"""
    c = client_as("player-sub")
    started = c.post("/api/snake/start").get_json()
    inputs, steps, _ = build_run(started["seed"])
    backdate(started["game_id"], 0.001)
    r = c.post("/api/snake/scores", json={
        "game_id": started["game_id"], "steps": steps, "inputs": inputs,
    })
    assert r.status_code == 400, r.status_code
    assert r.get_json()["error"] == "too_fast_to_be_real", r.get_json()


# ------------------------------------------------------------- ใครมาเล่นบ้าง
def test_a_finished_game_puts_the_player_on_the_admin_list():
    seed()
    assert play("player-sub").status_code == 200
    players = snapshot()["snake"]["players"]
    assert len(players) == 1, players
    assert players[0]["name"] == "player", players[0]
    assert players[0]["plays"] == 1, players[0]


def test_playing_again_counts_up_instead_of_adding_a_row():
    seed()
    for _ in range(3):
        assert play("player-sub").status_code == 200
    players = snapshot()["snake"]["players"]
    assert len(players) == 1, players
    assert players[0]["plays"] == 3, players[0]


def test_the_admin_list_and_the_public_board_agree():
    seed()
    scored = play("player-sub").get_json()["score"]
    assert scored > 0, "การเล่นที่จบจริงต้องมีแต้มเสมอ"

    players = snapshot()["snake"]["players"]
    board = client_as().get("/api/snake/scores").get_json()["top"]
    assert players[0]["score"] == scored, players[0]
    assert board == [{"name": "player", "score": scored}], board


def test_a_row_with_no_score_never_reaches_the_public_board():
    """กันไว้เผื่ออนาคต: แถวที่แต้มเป็น 0 มีไว้ให้แอดมินนับคนเล่น ห้ามโผล่หน้าสาธารณะ"""
    seed()
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO snake_scores (sub, name, score, best_at, updated_at, plays)"
            " VALUES (?,?,?,?,?,?)",
            ("player-sub", "player", 0, time.time(), time.time(), 1),
        )
    assert len(snapshot()["snake"]["players"]) == 1
    assert client_as().get("/api/snake/scores").get_json()["top"] == []


def test_admin_sees_an_email_to_contact_the_player_with():
    seed()
    play("player-sub")
    players = snapshot()["snake"]["players"]
    assert players[0]["email"] == "player@example.com", players[0]


def test_admin_list_still_hides_sub_and_credentials():
    """เหตุผลเดียวกับตารางผู้ใช้: อีเมลส่งได้ แต่ id ของ Google กับ token ห้ามหลุด"""
    seed()
    play("player-sub")
    body = client_as("owner-sub").get("/admin/data").get_data(as_text=True)
    assert SECRET not in body
    assert "player-sub" not in body and "owner-sub" not in body, "sub หลุดออกไปกับ JSON"


def test_the_score_records_the_speed_it_was_made_at():
    """ต้องรู้ว่าคะแนนนี้ทำตอนเกมเร็วแค่ไหน ไม่งั้นตัดสินคนชนะไม่ได้"""
    seed()
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 10})
    play("player-sub")
    players = snapshot()["snake"]["players"]
    assert players[0]["best_frames"] == 10, players[0]
    client_as("owner-sub").post("/admin/snake/speed", json={"frames": 6})


def test_totals_add_up():
    seed()
    play("player-sub")
    play("player-sub")
    play("quiet-sub")
    totals = snapshot()["snake"]["totals"]
    assert totals["players"] == 2, totals
    assert totals["plays"] == 3, totals


# ------------------------------------------------------------------ ลบประวัติ
def test_admin_can_delete_one_player():
    seed()
    play("player-sub")
    play("quiet-sub")
    players = snapshot()["snake"]["players"]
    target = [p for p in players if p["name"] == "player"][0]

    r = client_as("owner-sub").post("/admin/snake/delete", json={"id": target["id"]})
    assert r.status_code == 200, r.status_code
    left = r.get_json()["snake"]["players"]
    assert [p["name"] for p in left] == ["quiet"], left


def test_deleting_a_player_also_drops_their_pending_seeds():
    """ไม่งั้นเกมที่ค้างอยู่ส่งผลกลับมา แถวที่เพิ่งลบก็โผล่ขึ้นมาใหม่"""
    seed()
    play("player-sub")
    c = client_as("player-sub")
    started = c.post("/api/snake/start").get_json()
    inputs, steps, _ = build_run(started["seed"])

    target = snapshot()["snake"]["players"][0]
    client_as("owner-sub").post("/admin/snake/delete", json={"id": target["id"]})

    backdate(started["game_id"], 60)
    r = c.post("/api/snake/scores", json={
        "game_id": started["game_id"], "steps": steps, "inputs": inputs,
    })
    assert r.status_code == 400, "seed ที่ค้างอยู่ยังใช้ได้หลังลบผู้เล่นไปแล้ว"
    assert snapshot()["snake"]["players"] == []


def test_deleting_a_row_that_is_not_there():
    seed()
    r = client_as("owner-sub").post("/admin/snake/delete", json={"id": 99999})
    assert r.status_code == 404, r.status_code


def test_delete_needs_a_real_id():
    seed()
    c = client_as("owner-sub")
    for bad in ("1", None, True, 1.5):
        assert c.post("/admin/snake/delete", json={"id": bad}).status_code == 400, bad


def test_clearing_the_board_needs_confirmation():
    seed()
    play("player-sub")
    c = client_as("owner-sub")
    assert c.post("/admin/snake/clear", json={}).status_code == 400
    assert c.post("/admin/snake/clear", json={"confirm": "yes"}).status_code == 400
    assert len(snapshot()["snake"]["players"]) == 1, "ข้อมูลหายทั้งที่ยังไม่ได้ยืนยัน"


def test_clearing_the_board_removes_everyone():
    seed()
    play("player-sub")
    play("quiet-sub")
    r = client_as("owner-sub").post("/admin/snake/clear", json={"confirm": True})
    assert r.status_code == 200, r.status_code
    assert r.get_json()["snake"]["players"] == []
    assert client_as().get("/api/snake/scores").get_json()["top"] == []


def test_deleting_your_own_account_still_removes_your_score():
    """ของเดิมต้องไม่พัง — /privacy สัญญาว่าลบบัญชีแล้วข้อมูลหายหมด"""
    seed()
    play("player-sub")
    client_as("player-sub").post("/delete-account")
    assert snapshot()["snake"]["players"] == []


# --------------------------------------------------------- ของเดิมต้องไม่พัง
def test_the_rest_of_the_admin_snapshot_is_untouched():
    seed()
    d = snapshot()
    assert d["stats"] == {"total": 3, "active": 0, "configured": 0}, d["stats"]
    assert len(d["rows"]) == 3
    assert d["quota"]["budget"] == app.QUOTA_BUDGET


def test_admin_page_renders_with_the_snake_card():
    seed()
    play("player-sub")
    body = client_as("owner-sub").get("/admin").get_data(as_text=True)
    assert "Game speed" in body
    assert "player@example.com" in body
    assert SECRET not in body


def test_the_public_pages_still_work():
    for path in ("/", "/snake", "/viewtitle", "/flow", "/privacy", "/terms"):
        assert client_as().get(path).status_code == 200, path


def test_the_admin_panel_costs_no_youtube_quota():
    before = app.quota_used()
    c = client_as("owner-sub")
    for _ in range(10):
        c.get("/admin/data")
    c.post("/admin/snake/speed", json={"frames": 7})
    c.post("/admin/snake/speed", json={"frames": 6})
    assert app.quota_used() == before, "หน้า admin ไปกินโควตา YouTube"


def main():
    seed()
    # ตัวสร้างเกมตัวอย่างต้องใช้ได้ก่อน ไม่งั้นเทสต์ข้างล่างจะแดงด้วยเหตุผลที่ไม่เกี่ยว
    _, steps, score = build_run(12345)
    print(f"เกมตัวอย่าง: จบที่ก้าว {steps} ได้ {score} แต้ม\n")

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} ผ่าน")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
