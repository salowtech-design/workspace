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
          <div class="s-769bd11b">
            <span class="s-aa3ee35a">${data.time || ''}</span>
            <span class="evt-badge ${isSafe ? 'evt-ok' : 'evt-bad'}">${data.event}</span>
            <span class="s-03d9fc85">${data.employee_id}</span>
            <span class="s-c14ddfd0">[${data.step}] ${data.message}</span>
          </div>
          <div class="s-9bca2879">${data.reference_id} | ${data.network_address}</div>
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
