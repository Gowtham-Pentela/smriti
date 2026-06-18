/**
 * frontend/gdpr-consent.js
 * ─────────────────────────────────────────────────
 * Smriti Project - Hardened GDPR Consent Engine
 * Features: Granular Opt-Ins, State Persistence, Custom Event Hub, 
 * Right to be Forgotten Wipe Routine, and Fallback Defenses for Static Contexts.
 */

(function () {
  'use strict';

  const STORAGE_KEY = 'smriti-gdpr-consent';
  const CURRENT_VERSION = '1';

  // ── 1. Core API Definition ──────────────────────────────────────────────────
  window.SmritiConsent = {
    getRecord() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed.version === CURRENT_VERSION ? parsed : null;
      } catch (e) {
        return null;
      }
    },

    has(category) {
      if (category === 'functional') return true; // Always true
      const record = this.getRecord();
      return record ? !!record.choices[category] : false;
    },

    save(choices) {
      const record = {
        version: CURRENT_VERSION,
        timestamp: new Date().toISOString(),
        choices: {
          functional: true, // Enforce required state
          analytics: !!choices.analytics,
          marketing: !!choices.marketing
        }
      };

      localStorage.setItem(STORAGE_KEY, JSON.stringify(record));

      // CRITICAL EVENT MECHANIC: Dispatch event immediately while DOM branches are 100% active
      const event = new CustomEvent('smriti:consent-updated', { detail: record, bubbles: true });
      document.dispatchEvent(event);

      this.applyPreferences();
    },

    forgetMe() {
      // Clear tracking variables, tokens, and smriti application footprints
      const keysToRemove = [];
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && (
          key.startsWith('smriti-') ||
          key.startsWith('sb-') ||
          key.startsWith('supabase.')
        )) {
          keysToRemove.push(key);
        }
      }
      keysToRemove.forEach(k => localStorage.removeItem(k));

      // Standard tracking cookie extraction loop
      document.cookie.split(";").forEach(cookie => {
        const eqPos = cookie.indexOf("=");
        const name = eqPos > -1 ? cookie.substr(0, eqPos).trim() : cookie.trim();
        document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;`;
      });

      // Clear sessions and reload cleanly out of the tracking scope
      window.location.reload();
    },

    applyPreferences() {
      const banner = document.getElementById('smriti-gdpr-banner');
      const trigger = document.getElementById('smriti-gdpr-trigger');

      if (this.getRecord()) {
        if (banner) banner.style.display = 'none';
        if (trigger) trigger.style.display = 'flex';
      } else {
        if (banner) banner.style.display = 'block';
        if (trigger) trigger.style.display = 'none';
      }
    }
  };

  // ── 2. DOM Render & UI Generation ──────────────────────────────────────────
  function injectInterfaceMarkup() {
    if (document.getElementById('smriti-gdpr-banner')) return;

    // Create the persistent shell container markup strings
    const css = `
      #smriti-gdpr-banner { position: fixed; bottom: 20px; left: 20px; right: 20px; max-width: 600px; background: #1f2937; color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); z-index: 999999; font-family: sans-serif; display: none; }
      .gdpr-flex { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 15px; }
      .gdpr-btn { padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
      #gdpr-btn-accept { background: #10b981; color: white; }
      #gdpr-btn-reject { background: #4b5563; color: white; }
      #gdpr-btn-manage { background: transparent; color: #9ca3af; text-decoration: underline; }
      #smriti-gdpr-trigger { position: fixed; bottom: 20px; left: 20px; width: 44px; height: 44px; background: #3b82f6; border-radius: 50%; display: none; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.2); z-index: 999998; color: white; font-size: 20px; }
      #smriti-gdpr-modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 9999999; display: none; align-items: center; justify-content: center; }
      #smriti-gdpr-modal { background: #ffffff; color: #111827; max-width: 450px; width: 100%; padding: 25px; border-radius: 8px; font-family: sans-serif; }
      .modal-row { display: flex; justify-content: space-between; align-items: center; margin: 15px 0; }
      .modal-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
      #gdpr-btn-forget { background: #ef4444; color: white; margin-right: auto; }
    `;

    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    // Append Banner Markup Layout
    const bannerHtml = `
      <div id="smriti-gdpr-banner">
        <p style="margin:0;font-size:14px;line-height:1.5;">We care about your data privacy. We use essential cookies to keep our environment functional, and optional analytical tools to refine security tracking contexts.</p>
        <div class="gdpr-flex">
          <button class="gdpr-btn" id="gdpr-btn-accept">Accept all</button>
          <button class="gdpr-btn" id="gdpr-btn-reject">Reject optional</button>
          <button class="gdpr-btn" id="gdpr-btn-manage">Manage preferences</button>
        </div>
      </div>
      <div id="smriti-gdpr-trigger" title="Privacy Settings">🛡️</div>
    `;

    const bannerContainer = document.createElement('div');
    bannerContainer.innerHTML = bannerHtml;
    document.body.appendChild(bannerContainer);

    // Append Modal Shell Layout
    const modalHtml = `
      <div id="smriti-gdpr-modal-overlay">
        <div id="smriti-gdpr-modal">
          <h3 style="margin-top:0;">Privacy Preferences</h3>
          <div class="modal-row">
            <div><strong>Functional Cookies</strong><br><small style="color:#6b7280">Required for application authentication and state persistence.</small></div>
            <input type="checkbox" id="gdpr-toggle-functional" checked disabled>
          </div>
          <div class="modal-row">
            <div><strong>Analytics Tracking</strong><br><small style="color:#6b7280">Monitors performance, logging profiles, and workspace state security.</small></div>
            <input type="checkbox" id="gdpr-toggle-analytics">
          </div>
          <div class="modal-row">
            <div><strong>Marketing Systems</strong><br><small style="color:#6b7280">Enables dynamic outreach integrations and external announcements.</small></div>
            <input type="checkbox" id="gdpr-toggle-marketing">
          </div>
          <hr style="border:0;border-top:1px solid #e5e7eb;margin:20px 0;">
          <div class="modal-footer">
            <button class="gdpr-btn" id="gdpr-btn-forget">Wipe my data</button>
            <button class="gdpr-btn" id="gdpr-modal-save" style="background:#3b82f6;color:white;">Save Choices</button>
          </div>
        </div>
      </div>
    `;

    const modalContainer = document.createElement('div');
    modalContainer.innerHTML = modalHtml;
    document.body.appendChild(modalContainer);

    bindUIInteractions();
  }

  // ── 3. Interaction Mechanics & Control Binding ─────────────────────────────
  function bindUIInteractions() {
    const overlay = document.getElementById('smriti-gdpr-modal-overlay');
    const trigger = document.getElementById('smriti-gdpr-trigger');
    const acceptBtn = document.getElementById('gdpr-btn-accept');
    const rejectBtn = document.getElementById('gdpr-btn-reject');
    const manageBtn = document.getElementById('gdpr-btn-manage');
    const saveBtn = document.getElementById('gdpr-modal-save');
    const forgetBtn = document.getElementById('gdpr-btn-forget');

    const toggleAnalytics = document.getElementById('gdpr-toggle-analytics');
    const toggleMarketing = document.getElementById('gdpr-toggle-marketing');

    const closeModal = () => { if (overlay) overlay.style.display = 'none'; };
    const openModal = () => {
      if (overlay) overlay.style.display = 'flex';
      const record = window.SmritiConsent.getRecord();
      if (toggleAnalytics) toggleAnalytics.checked = record ? !!record.choices.analytics : false;
      if (toggleMarketing) toggleMarketing.checked = record ? !!record.choices.marketing : false;
    };

    acceptBtn?.addEventListener('click', () => {
      window.SmritiConsent.save({ analytics: true, marketing: true });
    });

    rejectBtn?.addEventListener('click', () => {
      window.SmritiConsent.save({ analytics: false, marketing: false });
    });

    manageBtn?.addEventListener('click', openModal);
    trigger?.addEventListener('click', openModal);

    saveBtn?.addEventListener('click', () => {
      window.SmritiConsent.save({
        analytics: !!toggleAnalytics?.checked,
        marketing: !!toggleMarketing?.checked
      });
      closeModal();
    });

    forgetBtn?.addEventListener('click', () => {
      if (confirm('Are you sure you want to exercise your right to be forgotten? This will completely wipe all session tracking indices and local profiles.')) {
        window.SmritiConsent.forgetMe();
      }
    });

    // BACKDROP INTERCEPTION FIX: Click target checks bounds accurately
    overlay?.addEventListener('click', (e) => {
      if (e.target === overlay) closeModal();
    });

    // ESCAPE KEY INTERCEPTION FIX: Explicit global window event listener
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && overlay && overlay.style.display === 'flex') {
        closeModal();
      }
    });
  }

  // ── 4. Resilient Runtime Execution ──────────────────────────────────────────
  async function initializeRuntime() {
    injectInterfaceMarkup();

    // Context Isolation Strategy: Avoid crashing execution paths if configuration fetches are slow
    try {
      if (typeof window._sbReady !== 'undefined') {
        await window._sbReady;
      }
      if (typeof window._sb !== 'undefined' && window._sb !== null) {
        // Safe attachment hook zone for application pipelines
      }
    } catch (err) {
      console.warn("GDPR Consent Manager localized a non-critical context gap safely:", err.message);
    }

    window.SmritiConsent.applyPreferences();
  }

  // Fire initialization cleanly once page document body context mounts securely
  function runWhenReady() {
    if (document.body) {
      initializeRuntime();
    } else {
      window.addEventListener('DOMContentLoaded', initializeRuntime, { once: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runWhenReady, { once: true });
  } else {
    runWhenReady();
  }

})();