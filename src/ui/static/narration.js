// Tellimations Narration Client
// Push-to-talk audio capture via MediaRecorder + WebSocket transport.

var NarrationClient = (function() {
  'use strict';

  var ws = null;
  var transcriptionBox = null;
  var feedbackEl = null;
  var pttHintEl = null;
  var mediaRecorder = null;
  var audioChunks = [];
  var isRecording = false;
  var idleTimer = null;
  var IDLE_TIMEOUT_MS = 10000;
  var stream = null;

  /**
   * Initialize the narration client.
   *
   * @param {WebSocket} websocket - Active WebSocket connection.
   * @param {HTMLElement} boxElement - The transcription box element.
   * @param {HTMLElement} feedbackElement - Element for transcription text.
   * @param {HTMLElement} hintElement - The "Hold Space to speak" hint element.
   */
  function init(websocket, boxElement, feedbackElement, hintElement) {
    ws = websocket;
    transcriptionBox = boxElement;
    feedbackEl = feedbackElement;
    pttHintEl = hintElement;

    // Request microphone access upfront
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      navigator.mediaDevices.getUserMedia({ audio: true })
        .then(function(s) {
          stream = s;
          setupKeyboardListeners();
        })
        .catch(function(err) {
          console.error('[NarrationClient] Microphone access denied:', err);
          pttHintEl.textContent = 'Microphone access required';
          pttHintEl.style.color = 'var(--red)';
        });
    } else {
      pttHintEl.textContent = 'Audio recording not supported in this browser';
      pttHintEl.style.color = 'var(--red)';
    }
  }

  function setupKeyboardListeners() {
    document.addEventListener('keydown', function(e) {
      if (e.code === 'Space' && !e.repeat && !isRecording) {
        e.preventDefault();
        startRecording();
      }
    });

    document.addEventListener('keyup', function(e) {
      if (e.code === 'Space' && isRecording) {
        e.preventDefault();
        stopRecording();
      }
    });
  }

  function startRecording() {
    if (!stream) return;

    isRecording = true;
    audioChunks = [];

    // Clear idle timer
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }

    // Update UI
    transcriptionBox.classList.add('recording');
    feedbackEl.textContent = '\uD83D\uDD34 Recording...';
    pttHintEl.style.visibility = 'hidden';

    // Start MediaRecorder
    try {
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    } catch (e) {
      // Fallback if audio/webm not supported
      mediaRecorder = new MediaRecorder(stream);
    }

    mediaRecorder.ondataavailable = function(event) {
      if (event.data.size > 0) {
        audioChunks.push(event.data);
      }
    };

    mediaRecorder.onstop = function() {
      var blob = new Blob(audioChunks, { type: 'audio/webm' });
      sendAudio(blob);
    };

    mediaRecorder.start();
  }

  function stopRecording() {
    if (!mediaRecorder || !isRecording) return;

    isRecording = false;
    mediaRecorder.stop();

    // Update UI
    transcriptionBox.classList.remove('recording');
    feedbackEl.textContent = '';
    pttHintEl.style.visibility = '';

    // Start idle timer
    resetIdleTimer();
  }

  function sendAudio(blob) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    // Read as ArrayBuffer and send as binary
    var reader = new FileReader();
    reader.onloadend = function() {
      // Send a JSON header first, then binary
      ws.send(JSON.stringify({
        type: 'audio',
        size: blob.size,
        mime_type: blob.type || 'audio/webm',
      }));
      ws.send(reader.result);
    };
    reader.readAsArrayBuffer(blob);
  }

  function resetIdleTimer() {
    if (idleTimer) {
      clearTimeout(idleTimer);
    }
    idleTimer = setTimeout(function() {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'idle_timeout' }));
      }
    }, IDLE_TIMEOUT_MS);
  }

  /**
   * Clean up resources.
   */
  function destroy() {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
    if (mediaRecorder && isRecording) {
      mediaRecorder.stop();
    }
    if (stream) {
      stream.getTracks().forEach(function(track) { track.stop(); });
      stream = null;
    }
    isRecording = false;
  }

  return {
    init: init,
    destroy: destroy,
    IDLE_TIMEOUT_MS: IDLE_TIMEOUT_MS,
  };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = NarrationClient;
}
