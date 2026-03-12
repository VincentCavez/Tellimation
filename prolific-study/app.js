// ============================================================
// CONFIGURATION — Update these before deploying
// ============================================================
const VIDEO_BASE_URL = "https://TO_BE_CONFIGURED.github.io/tellimations-study/videos/";
const SHEETS_ENDPOINT = "https://script.google.com/macros/s/AKfycbzLD2JBOkVoulfEy6UlQUIyo0TMEn2a6FhIQ9BgqjVpxNCI2rscsr75KKCdsKBktzNE/exec";
const PROLIFIC_RETURN_URL = "https://app.prolific.com/submissions/complete?cc=NOCODE";

// ============================================================
// STATE
// ============================================================
let config = null;
let participantId = null;
let assignedSet = null;
let latinSquareRow = null;
let stimuliOrder = [];
let currentPage = 0;
let pageStartTime = 0;
let currentStimulusRecord = {};

// Page layout:
// 0       = consent
// 1       = instructions
// 2       = training_arrow page A
// 3       = training_arrow page B
// 4       = training_cross page A
// 5       = training_cross page B
// 6       = transition
// 7..46   = 20 stimuli × 2 pages (A + B)
// 47      = end
const TOTAL_PAGES = 48;

// ============================================================
// INIT
// ============================================================
async function init() {
    try {
        const response = await fetch('config.json');
        config = await response.json();
    } catch (e) {
        document.getElementById('app').innerHTML =
            '<p style="color:red;padding:40px;">Error loading config.json. Please check the file exists.</p>';
        return;
    }

    DataCollector.init(SHEETS_ENDPOINT);

    // Get Prolific PID from URL
    const params = new URLSearchParams(window.location.search);
    participantId = params.get('PROLIFIC_PID') || 'debug_' + Date.now();

    // Check localStorage for existing assignment
    const saved = localStorage.getItem(`study_assignment_${participantId}`);
    if (saved) {
        const data = JSON.parse(saved);
        assignedSet = data.set;
        latinSquareRow = data.latinSquareRow;
        currentPage = data.currentPage || 0;
    } else {
        const hash = hashString(participantId);
        const position = Math.abs(hash) % 50;
        assignedSet = Math.floor(position / 10) + 1;
        latinSquareRow = position % 10;
        saveState();
    }

    // Build stimuli presentation order
    const setKey = `set${assignedSet}`;
    const setStimuli = config.sets[setKey];
    const actualLSRow = config.latin_square_selected_rows[latinSquareRow];
    const lsRow = config.latin_square[actualLSRow];
    stimuliOrder = lsRow.map(i => setStimuli[i]);

    renderPage(currentPage);
}

// ============================================================
// HASHING
// ============================================================
function hashString(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        hash = ((hash << 5) - hash) + str.charCodeAt(i);
        hash = hash & hash;
    }
    return hash;
}

// ============================================================
// STATE PERSISTENCE
// ============================================================
function saveState() {
    localStorage.setItem(`study_assignment_${participantId}`, JSON.stringify({
        set: assignedSet,
        latinSquareRow: latinSquareRow,
        currentPage: currentPage
    }));
}

function nextPage() {
    currentPage++;
    saveState();
    renderPage(currentPage);
    window.scrollTo(0, 0);
}

// ============================================================
// PAGE ROUTER
// ============================================================
function renderPage(page) {
    const app = document.getElementById('app');
    app.innerHTML = '';
    pageStartTime = Date.now();

    if (page === 0) renderConsent(app);
    else if (page === 1) renderInstructions(app);
    else if (page >= 2 && page <= 5) renderTrainingPage(app, page);
    else if (page === 6) renderTransition(app);
    else if (page >= 7 && page <= 46) renderStimulusPage(app, page);
    else if (page === 47) renderEnd(app);
}

// ============================================================
// PAGE: CONSENT
// ============================================================
function renderConsent(container) {
    const title = el('h1', 'Informed Consent', 'page-title');

    const consentBox = document.createElement('div');
    consentBox.className = 'consent-box';
    consentBox.innerHTML = `
        <h3>Study: Interpretability of Visual Animations</h3>
        <p>You are being invited to participate in a research study examining how people interpret short animations in a storytelling application designed for children.</p>
        <p><strong>What you will do:</strong></p>
        <ul>
            <li>Watch short animated clips (5–10 seconds each)</li>
            <li>Answer two questions about each clip: one open-ended and one multiple-choice</li>
            <li>The study takes approximately 15–20 minutes</li>
        </ul>
        <p><strong>Risks:</strong> There are no known risks beyond those of everyday computer use.</p>
        <p><strong>Benefits:</strong> Your responses will help improve educational technology for children.</p>
        <p><strong>Confidentiality:</strong> Your responses will be stored anonymously using your Prolific ID. No personally identifying information will be collected.</p>
        <p><strong>Voluntary participation:</strong> You may withdraw at any time by closing the browser window. Partial data may be retained.</p>
        <p><strong>Contact:</strong> If you have questions about this study, please contact the research team through Prolific.</p>
    `;

    const checkLabel = document.createElement('label');
    checkLabel.className = 'consent-checkbox';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkLabel.appendChild(checkbox);
    checkLabel.appendChild(document.createTextNode('I have read and understood the above information and agree to participate.'));

    const btnRow = document.createElement('div');
    btnRow.className = 'button-row';
    const btn = document.createElement('button');
    btn.className = 'btn-consent';
    btn.textContent = 'I agree and wish to participate';
    btn.disabled = true;
    btnRow.appendChild(btn);

    checkbox.addEventListener('change', () => {
        btn.disabled = !checkbox.checked;
    });

    btn.addEventListener('click', () => {
        DataCollector.send({
            participant_id: participantId,
            set: assignedSet,
            latin_square_row: latinSquareRow,
            type: 'consent',
            timestamp: new Date().toISOString()
        });
        nextPage();
    });

    container.append(title, consentBox, checkLabel, btnRow);
}

// ============================================================
// PAGE: INSTRUCTIONS
// ============================================================
function renderInstructions(container) {
    const title = el('h1', 'Instructions', 'page-title');

    const text = el('div', '', 'instructions-text');
    text.innerHTML = `
        <p>In this study, you will see scenes from a storytelling application designed for children.</p>
        <p>For each scene, you will first read what a child just said about the scene. Then you will watch a short animation that the system plays in response.</p>
        <p>Your task is to answer two questions about each animation.</p>
        <p>There are no right or wrong answers.</p>
        <p>The study takes approximately 15–20 minutes.</p>
    `;

    const btnRow = document.createElement('div');
    btnRow.className = 'button-row';
    const btn = document.createElement('button');
    btn.className = 'btn-next';
    btn.textContent = 'Start practice';
    btn.addEventListener('click', () => nextPage());
    btnRow.appendChild(btn);

    container.append(title, text, btnRow);
}

// ============================================================
// PAGES: TRAINING
// ============================================================
function renderTrainingPage(container, page) {
    const trainingIndex = Math.floor((page - 2) / 2);
    const isPageA = (page - 2) % 2 === 0;
    const trainingKey = trainingIndex === 0 ? 'arrow' : 'cross';
    const stimulus = config.training[trainingKey];

    const label = el('p', 'Practice', 'section-label');
    container.appendChild(label);

    if (isPageA) {
        renderOpenPage(container, stimulus, true);
    } else {
        renderClosedPage(container, stimulus, true);
    }
}

// ============================================================
// PAGE: TRANSITION
// ============================================================
function renderTransition(container) {
    const text = el('div', '', 'transition-text');
    text.innerHTML = `
        <p>The practice is over.</p>
        <p>The real study will now begin.</p>
        <p>Remember, there are no right or wrong answers.</p>
    `;

    const btnRow = document.createElement('div');
    btnRow.className = 'button-row';
    btnRow.style.justifyContent = 'center';
    const btn = document.createElement('button');
    btn.className = 'btn-next';
    btn.textContent = 'Begin study';
    btn.addEventListener('click', () => nextPage());
    btnRow.appendChild(btn);

    container.append(text, btnRow);
}

// ============================================================
// PAGES: STIMULUS
// ============================================================
function renderStimulusPage(container, page) {
    const stimulusIndex = Math.floor((page - 7) / 2);
    const isPageA = (page - 7) % 2 === 0;
    const stimulusId = stimuliOrder[stimulusIndex];
    const stimulus = config.stimuli[stimulusId];

    if (isPageA) {
        // Initialize record for this stimulus
        currentStimulusRecord = {
            participant_id: participantId,
            set: assignedSet,
            latin_square_row: latinSquareRow,
            stimulus_id: stimulusId,
            animation_id: stimulusId.split('_').slice(1).join('_'),
            scene_id: stimulusId.split('_')[0],
            presentation_order: stimulusIndex + 1,
            timestamp: new Date().toISOString()
        };
        renderOpenPage(container, stimulus, false);
    } else {
        renderClosedPage(container, stimulus, false);
    }
}

// ============================================================
// SHARED: OPEN RESPONSE PAGE (Page A)
// ============================================================
function renderOpenPage(container, stimulus, isTraining) {
    let videoPlayed = false;

    // Context
    const ctx = el('p', 'You are telling a story based on the elements you see in this scene.', 'context-line');

    // Utterance
    const uttLabel = el('p', 'Here is what you just said:', 'utterance-label');
    const uttBlock = document.createElement('div');
    uttBlock.className = 'utterance-block';
    uttBlock.appendChild(el('p', stimulus.utterance, 'utterance-text'));

    // System response label
    const respLabel = el('p', 'Here is how the system responds:', 'context-line');

    // Video
    const videoContainer = createVideoPlayer(VIDEO_BASE_URL + stimulus.video, () => {
        videoPlayed = true;
    });

    // Question
    const question = el('p', 'What would you say next?', 'question-text');

    // Textarea
    const textarea = document.createElement('textarea');
    textarea.className = 'response-textarea';
    textarea.placeholder = 'Type your response here...';

    // Next button
    const btn = document.createElement('button');
    btn.className = 'btn-next';
    btn.textContent = 'Next';
    btn.disabled = true;

    textarea.addEventListener('input', () => {
        btn.disabled = textarea.value.trim().length < 10;
    });

    btn.addEventListener('click', () => {
        if (!isTraining) {
            currentStimulusRecord.open_response = textarea.value.trim();
            currentStimulusRecord.open_response_time_ms = Date.now() - pageStartTime;
            currentStimulusRecord.video_played_page_a = videoPlayed;

            DataCollector.send({
                ...currentStimulusRecord,
                type: 'open_response'
            });
        }
        nextPage();
    });

    const btnRow = document.createElement('div');
    btnRow.className = 'button-row';
    btnRow.appendChild(btn);

    const sep = document.createElement('hr');
    sep.className = 'separator';

    container.appendChild(ctx);
    container.appendChild(uttLabel);
    container.appendChild(uttBlock);
    container.appendChild(respLabel);
    container.appendChild(videoContainer);
    container.appendChild(sep);
    container.appendChild(question);
    container.appendChild(textarea);
    container.appendChild(btnRow);
}

// ============================================================
// SHARED: CLOSED RESPONSE PAGE (Page B)
// ============================================================
function renderClosedPage(container, stimulus, isTraining) {
    let videoPlayed = false;
    let selectedAnswer = null;

    // Context
    const ctx = el('p', 'You are telling a story based on the elements you see in this scene.', 'context-line');

    // Utterance
    const uttLabel = el('p', 'Here is what you just said:', 'utterance-label');
    const uttBlock = document.createElement('div');
    uttBlock.className = 'utterance-block';
    uttBlock.appendChild(el('p', stimulus.utterance, 'utterance-text'));

    // System response label
    const respLabel = el('p', 'Here is how the system responds:', 'context-line');

    // Video
    const videoContainer = createVideoPlayer(VIDEO_BASE_URL + stimulus.video, () => {
        videoPlayed = true;
    });

    // Question
    const question = el('p', 'Which of the following best describes what this animation communicates?', 'question-text');

    // Radio buttons: shuffle first 3, keep last 2 fixed
    const first3 = shuffleArray([...stimulus.answers.slice(0, 3)]);
    const last2 = stimulus.answers.slice(3);
    const orderedAnswers = [...first3, ...last2];

    const radioGroup = document.createElement('div');
    radioGroup.className = 'radio-group';

    // Next button
    const btn = document.createElement('button');
    btn.className = 'btn-next';
    btn.textContent = 'Next';
    btn.disabled = true;

    orderedAnswers.forEach((answer, idx) => {
        const label = document.createElement('label');
        label.className = 'radio-option';

        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'closed_answer';
        radio.value = idx;

        const span = document.createElement('span');
        span.textContent = answer.text;

        label.appendChild(radio);
        label.appendChild(span);
        radioGroup.appendChild(label);

        radio.addEventListener('change', () => {
            // Update visual selection
            radioGroup.querySelectorAll('.radio-option').forEach(l => l.classList.remove('selected'));
            label.classList.add('selected');

            selectedAnswer = {
                index: idx,
                text: answer.text,
                correct: answer.correct
            };
            btn.disabled = false;
        });

        // Make entire label clickable
        label.addEventListener('click', (e) => {
            if (e.target !== radio) {
                radio.checked = true;
                radio.dispatchEvent(new Event('change'));
            }
        });
    });

    btn.addEventListener('click', () => {
        if (!isTraining) {
            currentStimulusRecord.closed_response = selectedAnswer.index;
            currentStimulusRecord.closed_response_correct = selectedAnswer.correct;
            currentStimulusRecord.closed_response_time_ms = Date.now() - pageStartTime;
            currentStimulusRecord.video_played_page_b = videoPlayed;

            DataCollector.send({
                ...currentStimulusRecord,
                type: 'complete'
            });
        }
        nextPage();
    });

    const btnRow = document.createElement('div');
    btnRow.className = 'button-row';
    btnRow.appendChild(btn);

    const sep = document.createElement('hr');
    sep.className = 'separator';

    container.appendChild(ctx);
    container.appendChild(uttLabel);
    container.appendChild(uttBlock);
    container.appendChild(respLabel);
    container.appendChild(videoContainer);
    container.appendChild(sep);
    container.appendChild(question);
    container.appendChild(radioGroup);
    container.appendChild(btnRow);
}

// ============================================================
// PAGE: END
// ============================================================
function renderEnd(container) {
    const title = el('h1', 'Thank you!', 'page-title');
    const text = el('p', 'Thank you for completing the study! Your responses have been recorded. Please click the button below to return to Prolific and submit your participation.', 'page-text');

    const btnRow = document.createElement('div');
    btnRow.className = 'button-row';
    btnRow.style.justifyContent = 'center';
    btnRow.style.marginTop = '32px';
    const btn = document.createElement('button');
    btn.className = 'btn-next';
    btn.textContent = 'Return to Prolific';
    btn.addEventListener('click', () => {
        window.location.href = PROLIFIC_RETURN_URL;
    });
    btnRow.appendChild(btn);

    container.append(title, text, btnRow);

    // Send completion event
    DataCollector.send({
        participant_id: participantId,
        set: assignedSet,
        latin_square_row: latinSquareRow,
        type: 'completion',
        timestamp: new Date().toISOString()
    });
}

// ============================================================
// HELPERS
// ============================================================

/**
 * Create a simple element with text and optional class.
 */
function el(tag, text, className) {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    if (className) e.className = className;
    return e;
}

/**
 * Create a video player with custom play overlay.
 * Plays once, then stays on last frame.
 */
function createVideoPlayer(videoUrl, onPlayed) {
    const wrapper = document.createElement('div');
    wrapper.className = 'video-container';

    const video = document.createElement('video');
    video.src = videoUrl;
    video.preload = 'auto';
    video.playsInline = true;

    const playBtn = document.createElement('button');
    playBtn.className = 'play-overlay';
    playBtn.innerHTML = '&#9654;';

    playBtn.addEventListener('click', () => {
        video.play();
        playBtn.classList.add('hidden');
    });

    video.addEventListener('ended', () => {
        if (onPlayed) onPlayed();
    });

    // Also mark as played if user somehow gets to the end
    video.addEventListener('pause', () => {
        if (video.currentTime >= video.duration - 0.1) {
            if (onPlayed) onPlayed();
        }
    });

    wrapper.appendChild(video);
    wrapper.appendChild(playBtn);
    return wrapper;
}

/**
 * Fisher-Yates shuffle (non-mutating).
 */
function shuffleArray(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

// ============================================================
// START
// ============================================================
init();
