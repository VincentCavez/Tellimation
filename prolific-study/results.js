/**
 * DataCollector — handles sending response data to the server endpoint
 * with localStorage fallback for failed requests.
 */
const DataCollector = {
    endpoint: null,

    init(endpoint) {
        this.endpoint = endpoint;
    },

    /**
     * Send a response record to the endpoint.
     * On failure, store in localStorage for retry.
     */
    async send(data) {
        // Always save to localStorage first as backup
        this._saveLocal(data);

        if (!this.endpoint || this.endpoint.includes('TO_BE_CONFIGURED')) {
            console.log('[DataCollector] Endpoint not configured, data saved locally:', data);
            return;
        }

        try {
            const response = await fetch(this.endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            // Mark as sent
            this._markSent(data);
            // Try sending any pending failed requests
            this._retryPending();
        } catch (e) {
            console.warn('[DataCollector] Send failed, stored for retry:', e.message);
            this._storePending(data);
        }
    },

    /**
     * Save data to localStorage backup (keyed by participant + stimulus + type).
     */
    _saveLocal(data) {
        const key = `response_${data.participant_id}_${data.stimulus_id || 'meta'}_${data.type || 'unknown'}`;
        localStorage.setItem(key, JSON.stringify(data));
    },

    _markSent(data) {
        const key = `response_${data.participant_id}_${data.stimulus_id || 'meta'}_${data.type || 'unknown'}`;
        const stored = localStorage.getItem(key);
        if (stored) {
            const parsed = JSON.parse(stored);
            parsed._sent = true;
            localStorage.setItem(key, JSON.stringify(parsed));
        }
    },

    /**
     * Store a failed request for later retry.
     */
    _storePending(data) {
        const pending = JSON.parse(localStorage.getItem('pending_requests') || '[]');
        // Avoid duplicates
        const key = `${data.participant_id}_${data.stimulus_id}_${data.type}`;
        const exists = pending.some(p =>
            `${p.participant_id}_${p.stimulus_id}_${p.type}` === key
        );
        if (!exists) {
            pending.push(data);
            localStorage.setItem('pending_requests', JSON.stringify(pending));
        }
    },

    /**
     * Retry sending any pending failed requests.
     */
    async _retryPending() {
        const pending = JSON.parse(localStorage.getItem('pending_requests') || '[]');
        if (pending.length === 0) return;

        const remaining = [];
        for (const data of pending) {
            try {
                const response = await fetch(this.endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                if (!response.ok) {
                    remaining.push(data);
                } else {
                    this._markSent(data);
                }
            } catch (e) {
                remaining.push(data);
            }
        }
        localStorage.setItem('pending_requests', JSON.stringify(remaining));
    },

    /**
     * Get all locally stored responses (for debugging).
     */
    getAllLocal(participantId) {
        const results = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key.startsWith(`response_${participantId}`)) {
                results.push(JSON.parse(localStorage.getItem(key)));
            }
        }
        return results;
    }
};
