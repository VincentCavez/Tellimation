// Tellimations Narration Client
// Push-to-talk audio capture via MediaRecorder + WebSocket transport.
// Voice guidance playback via Web Audio API.

var NarrationClient = (function() {
  'use strict';

  var ws = null;
  var transcriptionBox = null;
  var feedbackEl = null;
  var pttHintEl = null;
  var mediaRecorder = null;
  var audioChunks = [];
  var isRecording = false;
  var stream = null;

  // Voice playback state
  var audioContext = null;
  var pendingVoiceHeader = null;
  var pendingVoiceTimeout = null;
  var isPlayingVoice = false;
  var voiceQueue = [];  // Queue audio if something is already playing
  var wordRevealInterval = null;  // Timer for word-by-word TTS text display

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
        ensureAudioContext();
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

  // -----------------------------------------------------------------------
  // Audio recording (push-to-talk)
  // -----------------------------------------------------------------------

  /**
   * Cancel any in-progress word-by-word text reveal.
   */
  function _cancelWordReveal() {
    if (wordRevealInterval) {
      clearInterval(wordRevealInterval);
      wordRevealInterval = null;
    }
  }

  function startRecording() {
    if (!stream) return;

    isRecording = true;
    audioChunks = [];

    // Stop any ongoing word-by-word TTS text display
    _cancelWordReveal();

    // Stop looping animation when child starts recording
    if (window.animRunner && window.animRunner.stopLoop) {
      window.animRunner.stopLoop();
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

  // -----------------------------------------------------------------------
  // Voice guidance playback (Web Audio API)
  // -----------------------------------------------------------------------

  /**
   * Lazily initialise the AudioContext on the first user gesture.
   * Browsers require a user interaction before audio can play.
   */
  function ensureAudioContext() {
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: 24000,
      });
    }
    // Resume if suspended (Safari auto-suspends)
    if (audioContext.state === 'suspended') {
      audioContext.resume();
    }
  }

  /**
   * Handle a voice_audio JSON header from the server.
   * The next binary message will contain the PCM data.
   */
  function handleVoiceHeader(msg) {
    // Clear any stale pending header
    if (pendingVoiceTimeout) {
      clearTimeout(pendingVoiceTimeout);
    }

    pendingVoiceHeader = {
      purpose: msg.purpose || 'guidance',
      text: msg.text || '',
      sampleRate: msg.sample_rate || 24000,
      sampleWidth: msg.sample_width || 2,
      channels: msg.channels || 1,
    };

    // Safety: expire the header after 5s if no binary follows
    pendingVoiceTimeout = setTimeout(function() {
      pendingVoiceHeader = null;
      pendingVoiceTimeout = null;
    }, 5000);
  }

  /**
   * Handle a binary WebSocket message containing PCM audio data.
   * Must be preceded by a voice_audio JSON header.
   */
  function handleVoiceBinary(arrayBuffer) {
    if (pendingVoiceTimeout) {
      clearTimeout(pendingVoiceTimeout);
      pendingVoiceTimeout = null;
    }

    if (!pendingVoiceHeader) {
      // Binary without a header — ignore (could be something else)
      return;
    }

    var header = pendingVoiceHeader;
    pendingVoiceHeader = null;

    ensureAudioContext();

    if (isPlayingVoice) {
      // Queue if something is already playing
      voiceQueue.push({ header: header, buffer: arrayBuffer });
      return;
    }

    _playPCM(header, arrayBuffer);
  }

  /**
   * Decode PCM 16-bit signed mono and play via Web Audio API.
   * Simultaneously reveals the spoken text word-by-word in feedbackEl.
   */
  function _playPCM(header, arrayBuffer) {
    if (!audioContext) return;

    // Convert PCM 16-bit signed to Float32
    var pcmData = new Int16Array(arrayBuffer);
    var float32 = new Float32Array(pcmData.length);
    for (var i = 0; i < pcmData.length; i++) {
      float32[i] = pcmData[i] / 32768.0;
    }

    // Create AudioBuffer
    var buffer = audioContext.createBuffer(
      header.channels,
      float32.length,
      header.sampleRate
    );
    buffer.getChannelData(0).set(float32);

    // --- Word-by-word text reveal ---
    _cancelWordReveal();
    var words = (header.text || '').split(/\s+/).filter(Boolean);
    var durationSec = float32.length / header.sampleRate;

    if (feedbackEl && words.length > 0 && durationSec > 0) {
      feedbackEl.textContent = '';
      var wordIndex = 0;
      var intervalMs = (durationSec / words.length) * 1000;

      // Show the first word immediately
      feedbackEl.textContent = words[wordIndex];
      wordIndex++;

      if (words.length > 1) {
        wordRevealInterval = setInterval(function() {
          if (wordIndex < words.length) {
            feedbackEl.textContent += ' ' + words[wordIndex];
            wordIndex++;
          } else {
            clearInterval(wordRevealInterval);
            wordRevealInterval = null;
          }
        }, intervalMs);
      }
    }

    // Play
    var source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContext.destination);
    source.start();

    isPlayingVoice = true;
    source.onended = function() {
      // Flush: ensure all words are visible when audio ends
      _cancelWordReveal();
      if (feedbackEl && words.length > 0) {
        feedbackEl.textContent = words.join(' ');
      }

      isPlayingVoice = false;
      // Play next in queue if any
      if (voiceQueue.length > 0) {
        var next = voiceQueue.shift();
        _playPCM(next.header, next.buffer);
      }
    };
  }

  // -----------------------------------------------------------------------
  // Cleanup
  // -----------------------------------------------------------------------

  /**
   * Clean up resources.
   */
  function destroy() {
    _cancelWordReveal();
    if (pendingVoiceTimeout) {
      clearTimeout(pendingVoiceTimeout);
      pendingVoiceTimeout = null;
    }
    if (mediaRecorder && isRecording) {
      mediaRecorder.stop();
    }
    if (stream) {
      stream.getTracks().forEach(function(track) { track.stop(); });
      stream = null;
    }
    if (audioContext) {
      audioContext.close();
      audioContext = null;
    }
    isRecording = false;
    isPlayingVoice = false;
    voiceQueue = [];
  }

  return {
    init: init,
    destroy: destroy,
    handleVoiceHeader: handleVoiceHeader,
    handleVoiceBinary: handleVoiceBinary,
  };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = NarrationClient;
}
