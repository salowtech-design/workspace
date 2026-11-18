// Live SSE Security Stream Client for Salow IAM Operations
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('live-stream-items');
  const statusEl = document.getElementById('sse-status');
  if (!container || !statusEl) return;

  let lastEventId = null;

  function connect() {
    const streamUrl = '/auth/admin/iam/live-stream/' + (lastEventId ? '?last_id=' + encodeURIComponent(lastEventId) : '');
    const evtSource = new EventSource(streamUrl);

    evtSource.onopen = () => {
      statusEl.textContent = '● Live Stream Connected (Receiving real-time security events)';
      statusEl.style.color = '#10b981';
    };

    evtSource.addEventListener('auth_event', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.id) lastEventId = data.id;

        const placeholder = container.querySelector('.stream-placeholder');
        if (placeholder) placeholder.remove();

        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.justifyContent = 'space-between';
        row.style.padding = '0.4rem 0.6rem';
        row.style.borderRadius = '4px';
        row.style.background = '#1e293b';
        row.style.borderLeft = (data.code === 'INFO') ? '3px solid #10b981' : '3px solid #ef4444';
        row.style.color = '#f1f5f9';

        const isSafe = data.code === 'INFO';
        const badgeColor = isSafe ? '#059669' : '#dc2626';

        row.innerHTML = `
          <div style="display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap;">
            <span style="color: #94a3b8;">${data.time || ''}</span>
            <span style="padding: 0.15rem 0.4rem; border-radius: 3px; background: ${badgeColor}; font-weight: 700; font-size: 0.72rem;">${data.event}</span>
            <span style="font-weight: 600; color: #38bdf8;">${data.employee_id}</span>
            <span style="color: #cbd5e1;">[${data.step}] ${data.message}</span>
          </div>
          <div style="color: #64748b; font-size: 0.72rem;">${data.reference_id} | ${data.network_address}</div>
        `;

        container.insertBefore(row, container.firstChild);

        while (container.children.length > 50) {
          container.removeChild(container.lastChild);
        }
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    });

    evtSource.onerror = () => {
      statusEl.textContent = '○ Reconnecting to SSE stream...';
      statusEl.style.color = '#f59e0b';
      evtSource.close();
      setTimeout(connect, 3000);
    };
  }

  connect();
});
