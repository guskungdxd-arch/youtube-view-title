/* Snake — leaderboard + touch controls.
 *
 * This file is deliberately outside the game's line budget: it never edits the game,
 * it wraps endGame() from the outside and reads the globals the game already exposes.
 */
(function () {
  "use strict";

  var boardList = document.getElementById("board");
  var boardStatus = document.getElementById("boardStatus");
  var whoami = document.getElementById("whoami");
  var firstPerfect = document.getElementById("firstPerfect");

  var me = null;
  var perfectScore = 3960;

  /* ---------------------------------------------------------------- rendering */

  function renderWhoami() {
    if (me) {
      whoami.textContent = "Playing as " + me;
      return;
    }
    var link = document.createElement("a");
    link.href = "/login";
    link.textContent = "Sign in";
    whoami.replaceChildren(link, document.createTextNode(" to save your score"));
  }

  function row(text) {
    var li = document.createElement("li");
    li.className = "empty";
    li.textContent = text;
    return li;
  }

  function renderBoard(rows) {
    if (!rows.length) {
      boardList.replaceChildren(row("No scores yet — be the first"));
      return;
    }
    /* textContent, never innerHTML: these names come from other people. */
    boardList.replaceChildren.apply(boardList, rows.map(function (r, i) {
      var li = document.createElement("li");
      if (i === 0) li.classList.add("top");
      if (me && r.name === me) li.classList.add("me");
      if (r.score >= perfectScore) li.classList.add("perfect");

      var rank = document.createElement("span");
      rank.className = "rank";
      rank.textContent = String(i + 1).padStart(2, "0");

      var who = document.createElement("span");
      who.className = "who";
      who.textContent = r.name;

      var pts = document.createElement("span");
      pts.className = "pts";
      pts.textContent = r.score;

      li.append(rank, who, pts);
      return li;
    }));
  }

  /* ------------------------------------------------------------------ network */

  function renderFirstPerfect(first) {
    if (!first) {
      firstPerfect.textContent =
        "Nobody has filled the board yet. " + perfectScore + " is the only way.";
      firstPerfect.classList.remove("claimed");
      return;
    }
    firstPerfect.textContent =
      "First to fill the board: " + first.name + " on " + first.at;
    firstPerfect.classList.add("claimed");
  }

  function apply(data) {
    if (typeof data.perfect_score === "number") perfectScore = data.perfect_score;
    renderBoard(data.top);
    renderFirstPerfect(data.first_perfect);
  }

  function loadBoard() {
    return fetch("/api/snake/scores")
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        me = data.me;
        renderWhoami();
        apply(data);
        boardStatus.textContent = "";
      })
      .catch(function () {
        boardStatus.textContent = "Could not load the scoreboard";
      });
  }

  function submitRun(finalScore) {
    if (finalScore <= 0) return;
    if (!me) {
      boardStatus.textContent = "Sign in to put that on the board";
      return;
    }
    if (!runId) {
      boardStatus.textContent = "This run was not scored — reload for a fresh game";
      return;
    }

    var payload = { game_id: runId, steps: step, inputs: recorded };
    runId = null;                    // one seed, one run

    boardStatus.textContent = "Verifying…";
    fetch("/api/snake/scores", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        if (res.status === 401) {
          me = null;
          renderWhoami();
          boardStatus.textContent = "Session expired — sign in again";
          return null;
        }
        if (!res.ok) {
          // เซิร์ฟเวอร์เล่นซ้ำแล้วได้คนละผล = การเล่นชุดนี้เชื่อไม่ได้
          boardStatus.textContent = "That run did not check out";
          return null;
        }
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        apply(data);
        boardStatus.textContent = data.score >= perfectScore
          ? "Perfect run — the whole board"
          : "Verified — " + data.score;
      })
      .catch(function () {
        boardStatus.textContent = "Could not save that score";
      })
      .then(newRun);               // ขอ seed ใบใหม่ไว้รอเกมถัดไปเสมอ
  }

  /* --------------------------------------------------------- recording a run */

  /* The server hands out one seed per game and replays the whole thing to work out
     the score itself, so what gets sent is the run — every arrow key, and the step
     it was pressed on — not a number the browser could just make up. */
  var runId = null;
  var pending = null;              // seed ที่ขอมาแล้วรอเกมถัดไปหยิบไปใช้
  var recorded = [];

  function newRun() {
    if (!me) return Promise.resolve();
    return fetch("/api/snake/start", { method: "POST" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) { if (data) pending = data; })
      .catch(function () { pending = null; });
  }

  /* ------------------------------------------------- hook the game from outside */

  var coreEndGame = endGame;
  endGame = function () {
    coreEndGame();
    submitRun(score);
  };

  /* resetGame() ถูกเรียกตอนกดปุ่มใด ๆ หลังตาย — ต้องสวม seed ใบใหม่ตรงนี้
     ไม่งั้นเกมถัดไปจะเดินด้วย seed เดิมแล้วเซิร์ฟเวอร์ตรวจไม่ผ่าน */
  var coreResetGame = resetGame;
  resetGame = function () {
    recorded = [];
    if (pending) {
      runId = pending.game_id;
      // ตั้ง seed ก่อนเสมอ เพราะ resetGame วางอาหารชิ้นแรกทันทีด้วยตัวสุ่มนี้
      randState = pending.seed % 2147483647;
      if (randState <= 0) randState += 2147483646;
      pending = null;
    } else {
      runId = null;                // ไม่มี seed = เล่นได้ แต่คะแนนไม่ถูกนับ
    }
    coreResetGame();
  };

  /* ----------------------------------------------------------------- controls */

  /* Capture phase on purpose. The game listens on document and restarts on any key
     once you are dead; if this ran after it, the key that restarted the game would
     be recorded as the first input of the *next* run — one the game never applied —
     and the server's replay would diverge. Running first means gameOver still says
     what it said when the key was actually pressed.
     preventDefault also stops the arrows scrolling the page out from under you. */
  window.addEventListener("keydown", function (e) {
    if (e.which < 37 || e.which > 40) return;
    e.preventDefault();
    if (!gameOver) recorded.push([step, e.which]);
  }, { capture: true, passive: false });

  /* Swipe, so the page is playable on a phone at all. Mirrors the game's own rule:
     you may only turn onto the axis you are not currently moving along. */
  var canvasEl = document.getElementById("gameCanvas");
  var startX = 0;
  var startY = 0;
  var SWIPE_MIN = 24;

  canvasEl.addEventListener("touchstart", function (e) {
    startX = e.changedTouches[0].clientX;
    startY = e.changedTouches[0].clientY;
  }, { passive: true });

  canvasEl.addEventListener("touchend", function (e) {
    var dx = e.changedTouches[0].clientX - startX;
    var dy = e.changedTouches[0].clientY - startY;

    if (gameOver) {
      resetGame();
      return;
    }
    if (Math.abs(dx) < SWIPE_MIN && Math.abs(dy) < SWIPE_MIN) return;

    /* Go through the same keys the game already understands, so a swiped run and a
       typed run record identically and the server has only one thing to replay. */
    var key;
    if (Math.abs(dx) > Math.abs(dy)) {
      if (snake.dx !== 0) return;
      key = dx > 0 ? 39 : 37;
      snake.dx = dx > 0 ? grid : -grid;
      snake.dy = 0;
    } else {
      if (snake.dy !== 0) return;
      key = dy > 0 ? 40 : 38;
      snake.dy = dy > 0 ? grid : -grid;
      snake.dx = 0;
    }
    recorded.push([step, key]);
  }, { passive: true });

  loadBoard().then(newRun).then(function () {
    resetGame();                   // เกมแรกเริ่มด้วย seed จากเซิร์ฟเวอร์
  });
})();
