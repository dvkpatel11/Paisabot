/* ═══════════════════════════════════════════════════════════════════════════
   clock.js — Live ET market clock + session state indicator
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  const ET_TZ = 'America/New_York';

  // NYSE session times (ET)
  const PRE_OPEN   = { h: 4,  m: 0 };
  const OPEN       = { h: 9,  m: 30 };
  const CLOSE      = { h: 16, m: 0 };
  const AFTER_CLOSE= { h: 20, m: 0 };

  function getSessionState(now) {
    const h = now.hour(), m = now.minute();
    const mins = h * 60 + m;
    const preOpen    = PRE_OPEN.h * 60 + PRE_OPEN.m;
    const open       = OPEN.h    * 60 + OPEN.m;
    const close      = CLOSE.h   * 60 + CLOSE.m;
    const afterClose = AFTER_CLOSE.h * 60 + AFTER_CLOSE.m;

    const isWeekend = now.day() === 0 || now.day() === 6;
    if (isWeekend) return { label: 'CLOSED', cls: 'closed' };

    if (mins >= open  && mins < close)      return { label: 'OPEN',  cls: 'open'  };
    if (mins >= preOpen && mins < open)     return { label: 'PRE',   cls: 'pre'   };
    if (mins >= close && mins < afterClose) return { label: 'AFTER', cls: 'after' };
    return { label: 'CLOSED', cls: 'closed' };
  }

  function tick() {
    const now    = dayjs().tz(ET_TZ);
    const timeEl = document.getElementById('clock-time');
    const statEl = document.getElementById('clock-status');
    if (!timeEl || !statEl) return;

    timeEl.textContent = now.format('HH:mm:ss') + ' ET';

    const session = getSessionState(now);
    statEl.textContent = session.label;
    statEl.className   = `clock-status ${session.cls}`;
  }

  // Start immediately then every second
  document.addEventListener('DOMContentLoaded', () => {
    tick();
    setInterval(tick, 1000);
  });

})();
