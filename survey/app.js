/* ============================================================
   Tellimations Study 1 — Prolific Survey
   ============================================================ */

(function () {
  'use strict';

  // ==================== CONFIG ====================

  const CONFIG = {
    // Google Apps Script deployment URL — replace before launch
    API_BASE: 'https://script.google.com/macros/s/AKfycbyo0lTC_PxehUguHl0fpl_hEfZleL8Lp3YqBoeGEWjU9hLZAU4vJlGCz6GOo-fsNmlb/exec',

    // Asset path templates (relative to survey/index.html)
    imagePath: (sceneId) => `../data/prolific_gen/${sceneId}/hd/scene_1_full.png`,
    videoPath: (sceneId) => `../data/prolific_videos/${sceneId}.mp4`,

    // Data file paths
    dataFiles: {
      counterbalancing: '../data/counterbalancing_lists.json',
      block2: '../data/block2_assignments.json',
      allStimuli: '../data/study1_all_stimuli.json',
    },

    // Prolific
    completionCode: 'CS9JSJR2',
    prolificReturnUrl: 'https://app.prolific.com/submissions/complete?cc=CS9JSJR2',

    // Retry
    retryIntervalMs: 5000,
    flushTimeoutMs: 30000,

    // Demo mode: set to true to skip API calls and use mock assignment
    demoMode: false,
  };

  // ==================== SEEDED RNG ====================

  function hashString(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) {
      h = ((h << 5) - h + str.charCodeAt(i)) | 0;
    }
    return h >>> 0;
  }

  function mulberry32(seed) {
    return function () {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function seededShuffle(arr, seed) {
    const rng = mulberry32(seed);
    const shuffled = arr.slice();
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
  }

  // ==================== STATE ====================

  const state = {
    phase: 'LOADING',
    prolificId: null,
    slot: null,
    listId: null,
    participantId: null,

    // Loaded data
    allStimuliMap: null,     // Map: stimulus_id -> stimulus object
    counterbalancingLists: null,
    block2Assignments: null,

    // Trial sequences
    block1Trials: [],
    block2Trials: [],
    currentBlock: 1,
    currentTrialIndex: 0,

    // Per-trial state
    trialStartTime: null,
    videoPlayCount: 0,
    videoHasEnded: false,
    selectedOption: null,     // { code, text } for block1, { rating } for block2

    // Retry queue
    pendingResponses: [],
    flushingQueue: false,
  };

  // ==================== DATA LAYER ====================

  async function loadAllData() {
    const [counterbalancing, block2, allStimuli] = await Promise.all([
      fetch(CONFIG.dataFiles.counterbalancing).then((r) => r.json()),
      fetch(CONFIG.dataFiles.block2).then((r) => r.json()),
      fetch(CONFIG.dataFiles.allStimuli).then((r) => r.json()),
    ]);

    state.counterbalancingLists = counterbalancing;
    state.block2Assignments = block2;

    // Build lookup map
    state.allStimuliMap = new Map();
    for (const stim of allStimuli.stimuli) {
      state.allStimuliMap.set(stim.stimulus_id, stim);
    }
  }

  function buildBlock1Trials() {
    const list = state.counterbalancingLists.lists.find(
      (l) => l.list_id === state.listId
    );
    if (!list) throw new Error(`List ${state.listId} not found`);

    const trials = [];
    for (const entry of list.stimuli) {
      const sceneId = `study1_${entry.animation_id}_${entry.scene}`;
      const stimulusId = `${sceneId}_${entry.condition}`;
      const stim = state.allStimuliMap.get(stimulusId);
      if (!stim) {
        console.warn(`Stimulus not found: ${stimulusId}`);
        continue;
      }
      trials.push({
        ...stim,
        animation_id: entry.animation_id,
        scene: entry.scene,
        is_catch: false,
        imagePath: CONFIG.imagePath(sceneId),
        videoPath: CONFIG.videoPath(sceneId),
      });
    }

    // Shuffle with seeded RNG
    const seed = hashString(state.prolificId + '_block1');
    state.block1Trials = seededShuffle(trials, seed);
  }

  function buildBlock2Trials() {
    const participant = state.block2Assignments.participants.find(
      (p) => p.participant_id === state.participantId
    );
    if (!participant) throw new Error(`Participant ${state.participantId} not found`);

    const trials = [];
    for (const entry of participant.stimuli) {
      const sceneId = `study1_${entry.animation_id}_${entry.scene}`;
      const stimulusId = `${sceneId}_${entry.condition}`;
      const stim = state.allStimuliMap.get(stimulusId);
      if (!stim) {
        console.warn(`Stimulus not found: ${stimulusId}`);
        continue;
      }
      trials.push({
        ...stim,
        animation_id: entry.animation_id,
        scene: entry.scene,
        is_catch: false,
        imagePath: CONFIG.imagePath(sceneId),
        videoPath: CONFIG.videoPath(sceneId),
        pipeline_intent: stim.pipeline_intent || null,
      });
    }

    const seed = hashString(state.prolificId + '_block2');
    state.block2Trials = seededShuffle(trials, seed);
  }

  // ==================== API CLIENT ====================

  async function apiCall(action, payload) {
    if (CONFIG.demoMode) {
      console.log('[DEMO] API call:', action, payload);
      if (action === 'assign') {
        return { slot: 1, list_id: 1, participant_id: 1 };
      }
      return { ok: true };
    }

    const resp = await fetch(CONFIG.API_BASE, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({ action, ...payload }),
    });

    if (!resp.ok) throw new Error(`API ${action} failed: ${resp.status}`);
    return resp.json();
  }

  // ==================== RETRY QUEUE ====================

  function savePendingToStorage() {
    if (!state.prolificId) return;
    try {
      localStorage.setItem(
        `tellimations_pending_${state.prolificId}`,
        JSON.stringify(state.pendingResponses)
      );
    } catch (e) {
      // localStorage might be full or unavailable
    }
  }

  function loadPendingFromStorage() {
    if (!state.prolificId) return;
    try {
      const data = localStorage.getItem(`tellimations_pending_${state.prolificId}`);
      if (data) {
        const parsed = JSON.parse(data);
        if (Array.isArray(parsed) && parsed.length > 0) {
          state.pendingResponses = parsed;
        }
      }
    } catch (e) {
      // ignore
    }
  }

  function submitResponse(payload) {
    state.pendingResponses.push(payload);
    savePendingToStorage();
    flushQueue();
  }

  async function flushQueue() {
    if (state.flushingQueue) return;
    state.flushingQueue = true;

    while (state.pendingResponses.length > 0) {
      const item = state.pendingResponses[0];
      try {
        await apiCall('respond', item);
        state.pendingResponses.shift();
        savePendingToStorage();
      } catch (e) {
        console.warn('Response send failed, retrying in 5s:', e);
        state.flushingQueue = false;
        setTimeout(flushQueue, CONFIG.retryIntervalMs);
        return;
      }
    }

    state.flushingQueue = false;
  }

  async function flushAllPending() {
    return new Promise((resolve) => {
      if (state.pendingResponses.length === 0) {
        resolve();
        return;
      }

      const timeout = setTimeout(() => {
        console.warn('Flush timeout reached, some responses may be pending');
        resolve();
      }, CONFIG.flushTimeoutMs);

      const check = setInterval(() => {
        if (state.pendingResponses.length === 0) {
          clearInterval(check);
          clearTimeout(timeout);
          resolve();
        }
      }, 500);

      flushQueue();
    });
  }

  // ==================== SESSION PERSISTENCE ====================

  function saveSession() {
    if (!state.prolificId) return;
    try {
      localStorage.setItem(
        `tellimations_session_${state.prolificId}`,
        JSON.stringify({
          slot: state.slot,
          listId: state.listId,
          participantId: state.participantId,
          currentBlock: state.currentBlock,
          currentTrialIndex: state.currentTrialIndex,
        })
      );
    } catch (e) {
      // ignore
    }
  }

  function loadSession() {
    if (!state.prolificId) return null;
    try {
      const data = localStorage.getItem(`tellimations_session_${state.prolificId}`);
      return data ? JSON.parse(data) : null;
    } catch (e) {
      return null;
    }
  }

  // ==================== VIDEO CONTROLLER ====================

  const preloadPool = [];

  function preloadUpcoming(trials, currentIndex) {
    for (let i = 0; i < 2; i++) {
      const next = trials[currentIndex + 1 + i];
      if (next && next.videoPath) {
        if (!preloadPool[i]) {
          preloadPool[i] = document.createElement('video');
          preloadPool[i].preload = 'auto';
        }
        if (preloadPool[i].src !== next.videoPath) {
          preloadPool[i].src = next.videoPath;
          preloadPool[i].load();
        }
      }
    }
  }

  function setupVideoPlayer(trial) {
    const video = document.getElementById('video-player-1');
    const source = document.getElementById('video-source-1');
    const overlay = document.getElementById('video-overlay-1');
    const spinner = document.getElementById('video-spinner-1');
    const optionsContainer = document.getElementById('options-container-1');

    // Reset state
    state.videoPlayCount = 0;
    state.videoHasEnded = false;
    optionsContainer.classList.add('hidden');
    overlay.classList.remove('hidden');
    spinner.classList.add('hidden');

    // Clone video to clear all event listeners
    const newVideo = video.cloneNode(false);
    newVideo.id = 'video-player-1';
    newVideo.setAttribute('playsinline', '');
    video.parentNode.replaceChild(newVideo, video);

    // Set source
    const newSource = document.createElement('source');
    newSource.id = 'video-source-1';
    newSource.type = 'video/mp4';
    newSource.src = trial.videoPath;
    newVideo.appendChild(newSource);
    newVideo.load();

    // Loading state
    let canPlay = false;
    newVideo.addEventListener('canplay', () => {
      canPlay = true;
      spinner.classList.add('hidden');
    });

    // Overlay click -> play
    overlay.onclick = () => {
      if (!canPlay) {
        spinner.classList.remove('hidden');
      }
      overlay.classList.add('hidden');
      newVideo.play();
    };

    // Track plays
    newVideo.addEventListener('play', () => {
      state.videoPlayCount++;
    });

    // First ended -> show options
    newVideo.addEventListener('ended', () => {
      if (!state.videoHasEnded) {
        state.videoHasEnded = true;
        optionsContainer.classList.remove('hidden');
        // Scroll options into view
        optionsContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      // Show overlay again for replay
      overlay.classList.remove('hidden');
    });

    // Video failed to load -> show options anyway
    newVideo.addEventListener('error', () => {
      spinner.classList.add('hidden');
      overlay.classList.add('hidden');
      if (!state.videoHasEnded) {
        state.videoHasEnded = true;
        optionsContainer.classList.remove('hidden');
      }
    });
  }

  // ==================== RENDERING ====================

  function showScreen(screenId) {
    document.querySelectorAll('.screen').forEach((s) => s.classList.remove('active'));
    const screen = document.getElementById(screenId);
    if (screen) {
      screen.classList.add('active');
      window.scrollTo(0, 0);
    }
  }

  function renderWelcome() {
    showScreen('screen-welcome');

    const consentCb = document.getElementById('consent-checkbox');
    const idInput = document.getElementById('prolific-id-input');
    const startBtn = document.getElementById('start-btn');
    const errorEl = document.getElementById('welcome-error');

    // Auto-fill from URL
    const params = new URLSearchParams(window.location.search);
    const pid = params.get('PROLIFIC_PID') || params.get('prolific_pid') || '';
    if (pid) {
      idInput.value = pid;
    }

    function updateStartButton() {
      startBtn.disabled = !(consentCb.checked && idInput.value.trim().length > 0);
    }

    consentCb.onchange = updateStartButton;
    idInput.oninput = updateStartButton;
    updateStartButton();

    startBtn.onclick = async () => {
      const prolificId = idInput.value.trim();
      if (!prolificId) return;

      state.prolificId = prolificId;
      startBtn.disabled = true;
      startBtn.textContent = 'Assigning...';
      errorEl.classList.add('hidden');

      try {
        // Check for existing session
        const existingSession = loadSession();
        let assignment;

        if (existingSession) {
          assignment = {
            slot: existingSession.slot,
            list_id: existingSession.listId,
            participant_id: existingSession.participantId,
          };
          state.currentBlock = existingSession.currentBlock;
          state.currentTrialIndex = existingSession.currentTrialIndex;
        } else {
          assignment = await apiCall('assign', { prolific_id: prolificId });
        }

        if (assignment.error === 'full') {
          errorEl.textContent = 'Sorry, all study slots are currently filled. Thank you for your interest.';
          errorEl.classList.remove('hidden');
          startBtn.textContent = 'Start Study';
          startBtn.disabled = false;
          return;
        }

        if (assignment.error) {
          throw new Error(assignment.error);
        }

        state.slot = assignment.slot;
        state.listId = assignment.list_id;
        state.participantId = assignment.participant_id;

        // Load pending responses from storage
        loadPendingFromStorage();

        // Build trial sequences
        buildBlock1Trials();
        buildBlock2Trials();
        saveSession();

        // Transition to appropriate block
        if (state.currentBlock === 2) {
          transition('BLOCK2_TRIAL');
        } else {
          transition('BLOCK1_TRIAL');
        }
      } catch (e) {
        console.error('Assignment failed:', e);
        errorEl.textContent = 'Failed to connect to the study server. Please try again.';
        errorEl.classList.remove('hidden');
        startBtn.textContent = 'Start Study';
        startBtn.disabled = false;
      }
    };
  }

  function renderBlock1Trial() {
    showScreen('screen-block1');

    const trial = state.block1Trials[state.currentTrialIndex];
    const total = state.block1Trials.length;
    const current = state.currentTrialIndex + 1;

    // Progress
    document.getElementById('progress-bar-1').style.width =
      `${(current / total) * 100}%`;
    document.getElementById('progress-text-1').textContent =
      `${current} / ${total}`;

    // Scene image
    const img = document.getElementById('scene-image-1');
    img.style.display = '';
    img.src = trial.imagePath;
    img.onerror = () => {
      img.style.display = 'none';
    };
    img.onload = () => {
      img.style.display = '';
    };

    // Narrator text
    document.getElementById('narrator-text-1').textContent = trial.narrator_text;

    // Video
    setupVideoPlayer(trial);

    // Options
    const optionsDiv = document.getElementById('radio-options-1');
    optionsDiv.innerHTML = '';

    const optionEntries = Object.entries(trial.options);
    const optionSeed = hashString(state.prolificId + '_' + trial.stimulus_id);
    const shuffledOptions = seededShuffle(optionEntries, optionSeed);
    const displayOrder = shuffledOptions.map(([code]) => code);

    state.selectedOption = null;
    const nextBtn = document.getElementById('next-btn-1');
    nextBtn.disabled = true;

    shuffledOptions.forEach(([code, text], idx) => {
      const card = document.createElement('div');
      card.className = 'option-card';
      card.dataset.code = code;

      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'block1-option';
      radio.id = `opt-${idx}`;
      radio.value = code;

      const label = document.createElement('label');
      label.htmlFor = `opt-${idx}`;
      label.textContent = text;

      card.appendChild(radio);
      card.appendChild(label);

      card.onclick = () => {
        radio.checked = true;
        document.querySelectorAll('.option-card').forEach((c) =>
          c.classList.remove('selected')
        );
        card.classList.add('selected');
        state.selectedOption = { code, text };
        nextBtn.disabled = false;
      };

      optionsDiv.appendChild(card);
    });

    // Start timer
    state.trialStartTime = performance.now();

    // Preload next videos
    preloadUpcoming(state.block1Trials, state.currentTrialIndex);

    // Next button
    nextBtn.onclick = () => {
      if (!state.selectedOption) return;

      const responseTimeMs = Math.round(performance.now() - state.trialStartTime);

      const payload = {
        prolific_id: state.prolificId,
        slot: state.slot,
        block: 1,
        stimulus_id: trial.stimulus_id,
        scene_id: trial.scene_id,
        animation_id: trial.animation_id || trial.animation_id,
        condition: trial.condition,
        is_catch: trial.is_catch || false,
        selected_option_code: state.selectedOption.code,
        selected_option_text: state.selectedOption.text,
        option_display_order: displayOrder,
        response_time_ms: responseTimeMs,
        video_play_count: state.videoPlayCount,
        timestamp: new Date().toISOString(),
      };

      submitResponse(payload);

      // Advance
      state.currentTrialIndex++;
      saveSession();

      if (state.currentTrialIndex >= state.block1Trials.length) {
        state.currentBlock = 2;
        state.currentTrialIndex = 0;
        saveSession();
        transition('BLOCK1_TRANSITION');
      } else {
        renderBlock1Trial();
      }
    };
  }

  function renderTransition() {
    showScreen('screen-transition');
    document.getElementById('continue-btn').onclick = () => {
      transition('BLOCK2_TRIAL');
    };
  }

  function renderBlock2Trial() {
    showScreen('screen-block2');

    const trial = state.block2Trials[state.currentTrialIndex];
    const total = state.block2Trials.length;
    const current = state.currentTrialIndex + 1;

    // Progress
    document.getElementById('progress-bar-2').style.width =
      `${(current / total) * 100}%`;
    document.getElementById('progress-text-2').textContent =
      `${current} / ${total}`;

    // Scene image
    const img = document.getElementById('scene-image-2');
    img.style.display = '';
    img.src = trial.imagePath;
    img.onerror = () => {
      img.style.display = 'none';
    };
    img.onload = () => {
      img.style.display = '';
    };

    // Narrator text
    document.getElementById('narrator-text-2').textContent = trial.narrator_text;

    // Intent text
    const intentText = trial.pipeline_intent || 'Intent not available';
    document.getElementById('intent-text-2').textContent = intentText;

    // Reset Likert
    state.selectedOption = null;
    const nextBtn = document.getElementById('next-btn-2');
    nextBtn.disabled = true;

    const likertBtns = document.querySelectorAll('#likert-scale-2 .likert-btn');
    likertBtns.forEach((btn) => {
      btn.classList.remove('selected');
      btn.onclick = () => {
        likertBtns.forEach((b) => b.classList.remove('selected'));
        btn.classList.add('selected');
        state.selectedOption = { rating: parseInt(btn.dataset.value) };
        nextBtn.disabled = false;
      };
    });

    // Start timer
    state.trialStartTime = performance.now();

    // Next button
    nextBtn.onclick = () => {
      if (!state.selectedOption) return;

      const responseTimeMs = Math.round(performance.now() - state.trialStartTime);

      const payload = {
        prolific_id: state.prolificId,
        slot: state.slot,
        block: 2,
        stimulus_id: trial.stimulus_id,
        scene_id: trial.scene_id,
        animation_id: trial.animation_id,
        condition: trial.condition,
        likert_rating: state.selectedOption.rating,
        pipeline_intent: trial.pipeline_intent || '',
        response_time_ms: responseTimeMs,
        timestamp: new Date().toISOString(),
      };

      submitResponse(payload);

      // Advance
      state.currentTrialIndex++;
      saveSession();

      if (state.currentTrialIndex >= state.block2Trials.length) {
        transition('COMPLETING');
      } else {
        renderBlock2Trial();
      }
    };
  }

  async function renderCompletion() {
    showScreen('screen-done');

    const flushStatus = document.getElementById('flush-status');
    const codeEl = document.getElementById('completion-code');
    const redirectLink = document.getElementById('prolific-redirect');

    codeEl.textContent = CONFIG.completionCode;
    redirectLink.href = CONFIG.prolificReturnUrl;

    // Flush pending responses
    if (state.pendingResponses.length > 0) {
      flushStatus.classList.remove('hidden');
      await flushAllPending();
      flushStatus.classList.add('hidden');
    }

    // Mark as completed
    try {
      await apiCall('complete', {
        prolific_id: state.prolificId,
        slot: state.slot,
      });
    } catch (e) {
      console.warn('Failed to mark completion:', e);
    }

    // Clear session storage
    try {
      localStorage.removeItem(`tellimations_session_${state.prolificId}`);
      localStorage.removeItem(`tellimations_pending_${state.prolificId}`);
    } catch (e) {
      // ignore
    }
  }

  function renderError(message) {
    document.getElementById('error-message').textContent = message;
    showScreen('screen-error');
  }

  // ==================== STATE MACHINE ====================

  function transition(phase) {
    console.log(`[Survey] ${state.phase} → ${phase}`);
    state.phase = phase;

    switch (phase) {
      case 'WELCOME':
        renderWelcome();
        break;
      case 'BLOCK1_TRIAL':
        renderBlock1Trial();
        break;
      case 'BLOCK1_TRANSITION':
        renderTransition();
        break;
      case 'BLOCK2_TRIAL':
        renderBlock2Trial();
        break;
      case 'COMPLETING':
        renderCompletion();
        break;
      case 'ERROR':
        // message should be set before calling transition
        break;
      default:
        console.warn('Unknown phase:', phase);
    }
  }

  // ==================== INIT ====================

  async function init() {
    // Check demo mode from URL
    const params = new URLSearchParams(window.location.search);
    if (params.get('demo') === '1') {
      CONFIG.demoMode = true;
      console.log('[Survey] Demo mode enabled');
    }

    try {
      await loadAllData();
      console.log(`[Survey] Data loaded: ${state.allStimuliMap.size} stimuli`);
      transition('WELCOME');
    } catch (e) {
      console.error('Failed to load study data:', e);
      renderError('Failed to load study materials. Please refresh the page.');
      showScreen('screen-error');
    }
  }

  // Start
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
