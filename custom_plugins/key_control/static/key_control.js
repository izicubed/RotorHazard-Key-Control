/* KEY CONTROL - Run page panel, styled to match the Auto Marshalling panel
 * system (same dock, header geometry, fonts and chips).
 *
 * Header: Manual|Semi mode switch (like the Auto Marshalling school
 * selector) and the Semi confirmation window (seconds). Rows: keyboard ->
 * seat/pilot with the recent-lap dots (green confirmed / yellow unconfirmed /
 * blue button-only / red deleted). Footer: Calibrate flow (bind keyboards to
 * channels by pressing a key on each in turn), mapping reset and Link.
 * Driven by the server `button_kb_state` snapshot. */
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
	// Collapsed slim bar by default. Auto-expands while the forwarder link is
	// DOWN with the plugin enabled, during calibration, and during a running
	// race (the lap dots are the point of the panel); manual expand/collapse
	// by header click is session-only.
	function isOpen() {
		if (state && state.calibration) { return true; }
		if (state && state.enabled && state.forwarder && !state.forwarder.online) { return true; }
		if (userOpen !== null) { return userOpen; }
		if (state && state.enabled && state.race_status === 1) { return true; }
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
			'<div class="rh-bk-seg" title="Work mode. Manual: the timer’s own RSSI laps are suppressed, every lap comes from the buttons. Semi: the timer counts as usual and key presses confirm laps inside the window.">' +
			'<button class="rh-bk-mode" data-mode="manual">Manual</button>' +
			'<button class="rh-bk-mode" data-mode="semi">Semi</button></div>' +
			'<label class="rh-bk-thr" title="Semi confirmation window: a key press within this many seconds of a timer lap confirms it; a press with no timer lap inside the window adds a manual lap.">±<input type="number" min="0.2" max="30" step="0.1">s</label>' +
			'<span class="rh-bk-chip rh-bk-link"></span></div>' +
			'<div class="rh-bk-cal rh-bk-hidden"></div>' +
			'<div class="rh-bk-list"></div>' +
			'<div class="rh-bk-foot">' +
			'<button class="rh-bk-btn rh-bk-cal-btn">Calibrate</button>' +
			'<button class="rh-bk-btn rh-bk-reset-btn" title="Clear all fixed pins: keyboard N controls the Nth occupied seat again">Auto map</button>' +
			'<button class="rh-bk-btn rh-bk-link-btn" title="Contact the forwarder at the Key Control IP (Settings) and point it at this server">Link</button>' +
			'<span class="rh-bk-legend"><i class="rh-bk-dot rh-bk-m-green"></i>confirmed <i class="rh-bk-dot rh-bk-m-yellow"></i>unconfirmed <i class="rh-bk-dot rh-bk-m-blue"></i>manual <i class="rh-bk-dot rh-bk-m-red"></i>deleted</span>' +
			'</div>';
		panel.querySelector('.rh-bk-head').addEventListener('click', function (e) {
			if (e.target.closest('button, input, label')) { return; }
			userOpen = !isOpen();
			render();
		});
		Array.prototype.forEach.call(panel.querySelectorAll('.rh-bk-mode'), function (b) {
			b.addEventListener('click', function () {
				socket.emit('button_kb_set_mode', { mode: b.getAttribute('data-mode') });
			});
		});
		var thr = panel.querySelector('.rh-bk-thr input');
		thr.addEventListener('change', function () {
			var v = parseFloat(thr.value);
			if (!isNaN(v)) { socket.emit('button_kb_set_threshold', { sec: v }); }
		});
		panel.querySelector('.rh-bk-cal-btn').addEventListener('click', function () {
			var active = !!(state && state.calibration);
			socket.emit('button_kb_calibrate', { action: active ? 'cancel' : 'start' });
		});
		panel.querySelector('.rh-bk-reset-btn').addEventListener('click', function () {
			socket.emit('button_kb_calibrate', { action: 'reset' });
		});
		panel.querySelector('.rh-bk-link-btn').addEventListener('click', function () {
			socket.emit('button_kb_link', {});
		});
		d.appendChild(panel);
		return panel;
	}

	function q(sel) { return panel.querySelector(sel); }

	function lapDots(m) {
		var html = '<span class="rh-bk-laps">';
		var laps = m.laps || [];
		var last = null;
		laps.forEach(function (l) {
			var mark = l.mark === 'suppressed' ? 'red' : (l.mark || 'yellow');
			var what = l.n === 0 ? 'Holeshot' : (l.n == null || l.n < 0 ? 'Lap' : 'Lap ' + l.n);
			var title = what + ' · ' + (l.t || '') +
				(l.mark === 'suppressed' ? ' · suppressed (Manual mode)' :
				 l.deleted ? ' · deleted' :
				 l.mark === 'green' ? ' · confirmed' :
				 l.mark === 'blue' ? ' · manual (button)' : ' · unconfirmed');
			html += '<i class="rh-bk-dot rh-bk-m-' + mark +
				(l.mark === 'suppressed' ? ' rh-bk-m-hollow' : '') +
				'" title="' + esc(title) + '"></i>';
			last = l;
		});
		html += '</span>';
		if (last) {
			html += '<span class="rh-bk-lastlap rh-bk-t-' +
				(last.mark === 'suppressed' ? 'red' : (last.mark || 'yellow')) +
				(last.deleted || last.mark === 'suppressed' ? ' rh-bk-strike' : '') +
				'">' + esc(last.t || '') + '</span>';
		}
		return html;
	}

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
		var calBox = q('.rh-bk-cal');

		if (!state) {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-info';
			link.textContent = '…';
			mut.textContent = '';
			list.innerHTML = '';
			return;
		}

		// mode switch + threshold
		Array.prototype.forEach.call(panel.querySelectorAll('.rh-bk-mode'), function (b) {
			b.classList.toggle('rh-bk-mode-on',
				b.getAttribute('data-mode') === state.mode);
		});
		var thr = q('.rh-bk-thr input');
		if (document.activeElement !== thr) { thr.value = state.threshold; }
		q('.rh-bk-thr').classList.toggle('rh-bk-dim', state.mode !== 'semi');
		q('.rh-bk-legend').classList.toggle('rh-bk-hidden', state.mode !== 'semi');

		var fw = state.forwarder || {};
		if (!state.enabled) {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-warn';
			link.textContent = 'disabled';
			link.title = 'Plugin is disabled in Settings → KEY CONTROL';
		} else if (fw.online) {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-ok';
			link.textContent = 'link up';
			link.title = 'Forwarder connected' + (fw.host ? ' from ' + fw.host : '') +
				(fw.server ? ' → ' + fw.server : '');
		} else {
			link.className = 'rh-bk-chip rh-bk-link rh-bk-c-err';
			link.textContent = 'link down';
			link.title = 'No forwarder heartbeat — check the key-control-forwarder ' +
				'service' + (state.kc_ip ? ' at ' + state.kc_ip : '') +
				', or use Link after setting the Key Control IP';
		}

		// collapsed-header summary
		var mapping = state.mapping || [];
		var mapped = mapping.filter(function (m) { return m.seat != null; }).length;
		mut.textContent = (state.mode === 'manual' ? 'manual · ' : 'semi ±' + state.threshold + 's · ') +
			mapped + '/' + mapping.length +
			(fw.host && fw.online ? ' · ' + fw.host : '');

		// calibration banner
		var cal = state.calibration;
		calBox.classList.toggle('rh-bk-hidden', !cal);
		if (cal) {
			calBox.innerHTML = '<span class="rh-bk-cal-step">' + cal.step + '/' + cal.total +
				'</span> Press any key on the keyboard for <b>' + esc(cal.target) + '</b>';
		}
		q('.rh-bk-cal-btn').textContent = cal ? 'Cancel' : 'Calibrate';
		q('.rh-bk-cal-btn').classList.toggle('rh-bk-btn-cancel', !!cal);
		q('.rh-bk-link-btn').classList.toggle('rh-bk-dim', !state.kc_ip);
		q('.rh-bk-link-btn').title = state.kc_ip ?
			'Point the forwarder at ' + state.kc_ip + ' to this server' :
			'Set the Key Control IP in Settings → KEY CONTROL first';

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
						'title="Fixed seat (calibrated / pinned in Settings)">pin</span>' : '');
			}
			html += '<div class="rh-bk-row"><span class="rh-bk-kb">KB' + (m.kb + 1) +
				'</span>' + target + lapDots(m) +
				'<span class="rh-bk-live">' +
				'<i class="rh-bk-act rh-bk-act-add" data-kb="' + m.kb + '" data-btn="add"' +
				' title="key 1 — add / confirm lap"></i>' +
				'<i class="rh-bk-act rh-bk-act-del" data-kb="' + m.kb + '" data-btn="del"' +
				' title="key 2 — delete last lap"></i></span></div>';
		});
		list.innerHTML = html;
		applyLit();
	}

	// ------------------------------------------------- live press indicators
	// Server broadcasts `button_kb_press {kb, button}` the instant a key is
	// accepted; the matching circle lights up for a moment. Lit state lives
	// here (not in the DOM) so full re-renders can't wipe an active flash.
	var LIT_MS = 400;
	var lit = {};   // 'kb:btn' -> timestamp of last press

	function applyLit() {
		if (!panel) { return; }
		var now = Date.now();
		Array.prototype.forEach.call(panel.querySelectorAll('.rh-bk-act'), function (el2) {
			var key = el2.getAttribute('data-kb') + ':' + el2.getAttribute('data-btn');
			el2.classList.toggle('rh-bk-lit', now - (lit[key] || 0) < LIT_MS);
		});
	}

	function onPress(p) {
		if (!p || p.kb == null) { return; }
		lit[p.kb + ':' + (p.button === 'del' ? 'del' : 'add')] = Date.now();
		applyLit();
		setTimeout(applyLit, LIT_MS + 30);
	}

	function boot() {
		if (!onRunPage()) { return; }
		socket = window.socket || io();
		socket.on('button_kb_press', onPress);
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
