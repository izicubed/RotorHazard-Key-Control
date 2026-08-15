/* KEY CONTROL - Run page panel, styled to match the Auto
 * Marshalling panel system (same dock, header geometry, fonts and chips).
 * Driven by the server `button_kb_state` snapshot; requested on page load
 * and refreshed every few seconds. Collapsed to a slim bar by default; the
 * header always carries the link status so the bar alone tells the story. */
(function () {
	'use strict';

	if (window.__rhButtonKb) { return; }
	window.__rhButtonKb = true;
	if (typeof io === 'undefined') { return; }

	var socket = null, state = null, panel = null, userOpen = null;

	// ------------------------------------------------------------------ theme
	var theme = 'dark';
	var lightMq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
	function isLight() {
		if (theme === 'light') return true;
		if (theme === 'auto') return !!(lightMq && lightMq.matches);
		return false;
	}

	function ensureCss() {
		if (document.getElementById('rh-bk-css')) { return; }
		var l = document.createElement('link');
		l.id = 'rh-bk-css'; l.rel = 'stylesheet';
		l.href = '/key_control/static/key_control.css';
		(document.head || document.documentElement).appendChild(l);
	}
	function el(tag, cls, html) {
		var e = document.createElement(tag);
		if (cls) { e.className = cls; }
		if (html != null) { e.innerHTML = html; }
		return e;
	}
	function esc(s) {
		return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
			return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
		});
	}
	function onRunPage() { return !!document.getElementById('leaderboard'); }

	// Shared dock for our plugin panels above the pilot table on the Run page
	// (created by whichever of our plugins loads first; every plugin ships the
	// same dock rules, including centering).
	function dock() {
		var d = document.getElementById('rh-plugin-dock');
		if (d) { return d; }
		var anchor = document.getElementById('leaderboard');
		if (!anchor || !anchor.parentNode) { return null; }
		d = el('div', 'rh-plugin-dock');
		d.id = 'rh-plugin-dock';
		anchor.parentNode.insertBefore(d, anchor);
		return d;
	}

	// -------------------------------------------------------------- collapse
	// Collapsed slim bar by default. Auto-expands only while the forwarder
	// link is DOWN with the plugin enabled (the operator must notice); manual
	// expand/collapse by header click is session-only.
	function isOpen() {
		if (state && state.enabled && state.forwarder && !state.forwarder.online) { return true; }
		if (userOpen !== null) { return userOpen; }
		return false;
	}

	function ensurePanel() {
		if (panel) { return panel; }
		var d = dock();
		if (!d) { return null; }
		panel = el('div', 'rh-bk');
		panel.id = 'rh-bk';
		panel.innerHTML =
			'<div class="rh-bk-head"><span class="rh-bk-chev">▸</span>' +
			'<div class="rh-bk-title"><span class="rh-bk-spark">⌨</span> Key Control</div>' +
			'<span class="rh-bk-headmut"></span>' +
			'<span class="rh-bk-chip rh-bk-link"></span></div>' +
			'<div class="rh-bk-list"></div>';
		panel.querySelector('.rh-bk-head').addEventListener('click', function (e) {
			if (e.target.closest('button, input')) { return; }
			userOpen = !isOpen();
			render();
		});
		d.appendChild(panel);
		return panel;
	}

	function q(sel) { return panel.querySelector(sel); }

	function render() {
		if (!onRunPage()) { return; }
		ensureCss();
		if (!ensurePanel()) { return; }
		panel.classList.toggle('rh-bk-light', isLight());

		var open = isOpen();
		panel.classList.toggle('rh-bk-collapsed', !open);
		q('.rh-bk-chev').textContent = open ? '▾' : '▸';

		var link = q('.rh-bk-link');
		var mut = q('.rh-bk-headmut');
		var list = q('.rh-bk-list');

		if (!state) {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-info';
			link.textContent = '…';
			mut.textContent = '';
			list.innerHTML = '';
			return;
		}

		var fw = state.forwarder || {};
		if (!state.enabled) {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-warn';
			link.textContent = 'disabled';
			link.title = 'Plugin is disabled in Settings → USB Button Keyboards';
		} else if (fw.online) {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-ok';
			link.textContent = 'link up';
			link.title = 'Forwarder connected' + (fw.host ? ' from ' + fw.host : '');
		} else {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-err';
			link.textContent = 'link down';
			link.title = 'No forwarder heartbeat — check the ' +
				'button-keyboards-forwarder service on the keyboard Pi';
		}

		// collapsed-header summary: enough context without expanding
		var mapping = state.mapping || [];
		var mapped = mapping.filter(function (m) { return m.seat != null; }).length;
		mut.textContent = mapped + '/' + mapping.length + ' mapped' +
			(fw.host && fw.online ? ' · ' + fw.host : '');

		var html = '';
		if (!mapping.length) {
			html = '<div class="rh-bk-empty">No keyboards configured.</div>';
		}
		mapping.forEach(function (m) {
			var target;
			if (m.seat == null) {
				target = '<span class="rh-bk-none" title="' + esc(m.problem || '') + '">' +
					'not mapped' + (m.problem ? ' — ' + esc(m.problem) : '') + '</span>';
			} else {
				target = '<span class="rh-bk-seat">' +
					esc(m.label || ('S' + (m.seat + 1))) + '</span>' +
					'<span class="rh-bk-name">' + esc(m.callsign || '—') + '</span>' +
					(m.fixed ? '<span class="rh-bk-chip rh-bk-c-info" ' +
						'title="Fixed seat override (Settings)">pinned</span>' : '');
			}
			html += '<div class="rh-bk-row"><span class="rh-bk-kb">KB' + (m.kb + 1) +
				'</span>' + target +
				'<span class="rh-bk-keys"><kbd>1</kbd>+ lap <kbd>2</kbd>− lap</span></div>';
		});
		list.innerHTML = html;
	}

	function boot() {
		if (!onRunPage()) { return; }
		socket = window.socket || io();
		socket.on('button_kb_state', function (s) {
			state = s || {};
			if (state.theme) { theme = state.theme; }
			render();
		});
		if (lightMq && lightMq.addEventListener) {
			lightMq.addEventListener('change', render);
		}
		function ask() { try { socket.emit('button_kb_get_state'); } catch (e) { } }
		ask();
		setInterval(ask, 5000);
		render();
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', boot);
	} else {
		boot();
	}
})();
