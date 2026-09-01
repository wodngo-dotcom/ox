(() => {
  "use strict";

  const QUESTION_TIME_MS = 10000;
  const TOP_SCORES_KEY = "oxQuizTopScores";
  const MAX_RANK = 3;
  const RANK_MEDALS = ["🥇", "🥈", "🥉"];
  const GAUGE_CIRCUMFERENCE = 2 * Math.PI * 45; // r=45

  // ---------- DOM ----------
  const startScreen = document.getElementById("start-screen");
  const gameScreen = document.getElementById("game-screen");
  const nameScreen = document.getElementById("name-screen");
  const gameoverScreen = document.getElementById("gameover-screen");

  const startBtn = document.getElementById("start-btn");
  const retryBtn = document.getElementById("retry-btn");
  const startBestEl = document.getElementById("start-best");

  const gaugeFg = document.getElementById("gauge-fg");
  const scoreEl = document.getElementById("score");
  const questionTextEl = document.getElementById("question-text");
  const btnO = document.getElementById("btn-o");
  const btnX = document.getElementById("btn-x");
  const explainBox = document.getElementById("explain-box");
  const explainBadge = document.getElementById("explain-badge");
  const explainText = document.getElementById("explain-text");
  const effectOverlay = document.getElementById("effect-overlay");
  const effectEmoji = document.getElementById("effect-emoji");

  const nameScoreEl = document.getElementById("name-score");
  const nameInput = document.getElementById("name-input");
  const nameConfirmBtn = document.getElementById("name-confirm-btn");

  const gameoverTitle = document.getElementById("gameover-title");
  const gameoverMessage = document.getElementById("gameover-message");
  const finalScoreEl = document.getElementById("final-score");
  const rankTableEl = document.getElementById("rank-table");

  // ---------- 상태 ----------
  let score = 0;
  let currentQuestion = null;
  let answered = false;
  let tickIntervalId = null;
  let timeoutId = null;
  let audioCtx = null;

  // ---------- 오디오 (Web Audio API로 효과음 생성) ----------
  function getAudioCtx() {
    if (!audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      audioCtx = new Ctx();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    return audioCtx;
  }

  function playTone(freq, startTime, duration, type, gainPeak) {
    const ctx = getAudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type || "sine";
    osc.frequency.setValueAtTime(freq, startTime);
    gain.gain.setValueAtTime(0.0001, startTime);
    gain.gain.exponentialRampToValueAtTime(gainPeak || 0.2, startTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(startTime);
    osc.stop(startTime + duration + 0.02);
  }

  function playTick() {
    try {
      const ctx = getAudioCtx();
      const now = ctx.currentTime;
      playTone(1200, now, 0.06, "square", 0.08);
    } catch (e) { /* 오디오 미지원 브라우저 무시 */ }
  }

  function playCorrectSound() {
    try {
      const ctx = getAudioCtx();
      const now = ctx.currentTime;
      playTone(660, now, 0.15, "sine", 0.22);
      playTone(880, now + 0.12, 0.18, "sine", 0.22);
      playTone(1100, now + 0.26, 0.22, "sine", 0.22);
    } catch (e) { /* noop */ }
  }

  function playWrongSound() {
    try {
      const ctx = getAudioCtx();
      const now = ctx.currentTime;
      playTone(260, now, 0.4, "sine", 0.15);
      playTone(220, now + 0.15, 0.4, "sine", 0.12);
    } catch (e) { /* noop */ }
  }

  // ---------- TTS ----------
  let koVoice = null;
  function pickKoreanVoice() {
    if (!window.speechSynthesis) return;
    const voices = window.speechSynthesis.getVoices();
    koVoice = voices.find((v) => v.lang && v.lang.toLowerCase().startsWith("ko")) || null;
  }
  if (window.speechSynthesis) {
    pickKoreanVoice();
    window.speechSynthesis.onvoiceschanged = pickKoreanVoice;
  }

  function speak(text) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) {
        resolve();
        return;
      }
      window.speechSynthesis.cancel();

      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = "ko-KR";
      if (koVoice) utter.voice = koVoice;
      utter.rate = 0.95;
      utter.pitch = 1.05;

      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(fallback);
        resolve();
      };

      utter.onend = finish;
      utter.onerror = finish;

      // 일부 브라우저에서 onend가 호출되지 않는 경우를 대비한 안전장치
      const estMs = Math.min(8000, Math.max(1500, text.length * 160));
      const fallback = setTimeout(finish, estMs);

      window.speechSynthesis.speak(utter);
    });
  }

  // ---------- TOP3 랭킹 저장 ----------
  function getTopScores() {
    try {
      const raw = localStorage.getItem(TOP_SCORES_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(arr)) return [];
      return arr.filter((e) => e && typeof e.score === "number" && typeof e.name === "string");
    } catch (e) {
      return [];
    }
  }

  function saveTopScores(list) {
    localStorage.setItem(TOP_SCORES_KEY, JSON.stringify(list));
  }

  function qualifiesForTopScores(list, candidateScore) {
    if (list.length < MAX_RANK) return true;
    return candidateScore > list[list.length - 1].score;
  }

  function insertTopScore(list, entry) {
    const newList = list.concat([entry]);
    newList.sort((a, b) => b.score - a.score);
    return newList.slice(0, MAX_RANK);
  }

  function renderRankTable(list, highlightIndex) {
    rankTableEl.innerHTML = "";
    if (list.length === 0) {
      const row = document.createElement("div");
      row.className = "rank-row empty";
      row.textContent = "아직 기록이 없어요";
      rankTableEl.appendChild(row);
      return;
    }
    list.forEach((entry, i) => {
      const row = document.createElement("div");
      row.className = "rank-row" + (i === highlightIndex ? " new" : "");

      const medal = document.createElement("span");
      medal.className = "rank-medal";
      medal.textContent = RANK_MEDALS[i] || "🎖️";

      const name = document.createElement("span");
      name.className = "rank-name";
      name.textContent = entry.name;

      const points = document.createElement("span");
      points.className = "rank-score";
      points.textContent = `${entry.score}개`;

      row.appendChild(medal);
      row.appendChild(name);
      row.appendChild(points);
      rankTableEl.appendChild(row);
    });
  }

  // ---------- 게이지 타이머 ----------
  function resetGaugeInstant() {
    gaugeFg.style.transition = "none";
    gaugeFg.style.strokeDashoffset = "0";
    gaugeFg.classList.remove("warn", "danger");
    // 강제 리플로우로 트랜지션 리셋 반영
    void gaugeFg.getBoundingClientRect();
  }

  function startGaugeCountdown() {
    resetGaugeInstant();
    requestAnimationFrame(() => {
      gaugeFg.style.transition = `stroke-dashoffset ${QUESTION_TIME_MS}ms linear`;
      gaugeFg.style.strokeDashoffset = String(GAUGE_CIRCUMFERENCE);
    });
  }

  function freezeGauge() {
    const computed = getComputedStyle(gaugeFg).strokeDashoffset;
    gaugeFg.style.transition = "none";
    gaugeFg.style.strokeDashoffset = computed;
  }

  function startTimer() {
    answered = false;
    startGaugeCountdown();

    let tickCount = 0;
    tickIntervalId = setInterval(() => {
      tickCount++;
      playTick();
      if (tickCount === 7) gaugeFg.classList.add("warn");
      if (tickCount === 9) gaugeFg.classList.add("danger");
    }, 1000);

    timeoutId = setTimeout(() => {
      handleAnswer(null);
    }, QUESTION_TIME_MS);
  }

  function stopTimer() {
    clearInterval(tickIntervalId);
    clearTimeout(timeoutId);
    tickIntervalId = null;
    timeoutId = null;
    freezeGauge();
  }

  // ---------- 이펙트 ----------
  function showEffect(isCorrect) {
    return new Promise((resolve) => {
      effectOverlay.classList.remove("hidden", "correct", "wrong");
      effectEmoji.textContent = isCorrect ? "✨🌟✨" : "😊💭";
      effectOverlay.classList.add(isCorrect ? "correct" : "wrong");
      if (isCorrect) playCorrectSound();
      else playWrongSound();

      setTimeout(() => {
        effectOverlay.classList.add("hidden");
        effectOverlay.classList.remove("correct", "wrong");
        resolve();
      }, 750);
    });
  }

  // ---------- 문제 진행 ----------
  function pickRandomQuestion() {
    const idx = Math.floor(Math.random() * QUESTIONS.length);
    return QUESTIONS[idx];
  }

  function showQuestion() {
    currentQuestion = pickRandomQuestion();
    questionTextEl.textContent = currentQuestion.q;
    explainBox.classList.add("hidden");
    btnO.disabled = false;
    btnX.disabled = false;

    speak(currentQuestion.q);
    startTimer();
  }

  async function handleAnswer(choice) {
    if (answered) return;
    answered = true;
    stopTimer();
    btnO.disabled = true;
    btnX.disabled = true;

    const isCorrect = choice === currentQuestion.a;

    if (isCorrect) {
      score++;
      scoreEl.textContent = String(score);
    }

    await showEffect(isCorrect);

    explainBox.classList.remove("hidden");
    explainBadge.textContent = isCorrect ? "정답이에요! 😊" : "괜찮아요! 😊";
    explainBadge.className = "explain-badge " + (isCorrect ? "correct" : "wrong");
    const answerLabel = currentQuestion.a === "O" ? "O" : "X";
    const fullExplain = `정답은 ${answerLabel}예요! ${currentQuestion.e}`;
    explainText.textContent = fullExplain;

    await speak(fullExplain);
    await wait(500);

    if (isCorrect) {
      showQuestion();
    } else {
      endGame();
    }
  }

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // ---------- 화면 전환 ----------
  function showScreen(screen) {
    [startScreen, gameScreen, nameScreen, gameoverScreen].forEach((s) => s.classList.add("hidden"));
    screen.classList.remove("hidden");
  }

  function startGame() {
    getAudioCtx();
    score = 0;
    scoreEl.textContent = "0";
    showScreen(gameScreen);
    showQuestion();
  }

  function endGame() {
    window.speechSynthesis && window.speechSynthesis.cancel();
    const topScores = getTopScores();

    if (qualifiesForTopScores(topScores, score)) {
      showNameScreen();
    } else {
      showResultScreen(topScores, null);
    }
  }

  function showNameScreen() {
    nameScoreEl.textContent = String(score);
    nameInput.value = "";
    showScreen(nameScreen);
    speak(`${score}개를 맞혀서 TOP3에 들었어요! 이름을 입력해주세요`);
    setTimeout(() => nameInput.focus(), 100);
  }

  function submitName() {
    const rawName = nameInput.value.trim();
    const name = rawName ? rawName.slice(0, 6) : "이름없음";
    const entry = { name, score };

    const updated = insertTopScore(getTopScores(), entry);
    saveTopScores(updated);

    const newIndex = updated.indexOf(entry);
    showResultScreen(updated, newIndex);
  }

  function showResultScreen(topScores, newIndex) {
    finalScoreEl.textContent = String(score);
    renderRankTable(topScores, newIndex);

    const madeTop3 = newIndex !== null;
    if (madeTop3) {
      gameoverTitle.textContent = "🎉 TOP3 진입! 🎉";
      gameoverMessage.textContent = "정말 잘했어요! 대단해요!";
    } else {
      gameoverTitle.textContent = "🎈 잘했어요! 🎈";
      gameoverMessage.textContent = "괜찮아요, 잘했어요! 다시 도전해볼까요?";
    }

    showScreen(gameoverScreen);
    speak(madeTop3
      ? `TOP3에 들었어요! ${score}개를 맞혔어요! 정말 잘했어요!`
      : `${score}개를 맞혔어요. 괜찮아요, 잘했어요! 다시 도전해볼까요?`);

    startBestEl.textContent = String(getTopScores()[0] ? getTopScores()[0].score : 0);
  }

  // ---------- 이벤트 ----------
  startBtn.addEventListener("click", startGame);
  retryBtn.addEventListener("click", startGame);
  btnO.addEventListener("click", () => handleAnswer("O"));
  btnX.addEventListener("click", () => handleAnswer("X"));
  nameConfirmBtn.addEventListener("click", submitName);
  nameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitName();
  });

  // ---------- 초기화 ----------
  startBestEl.textContent = String(getTopScores()[0] ? getTopScores()[0].score : 0);
})();
