/*******************************************
 * Original LN Donation / Guestbook Logic
 * (Unchanged except minor wording)
 *******************************************/

let totalDonations = 0;
let transactionsData = [];
const rowsPerPage = 17;
let currentPage = 1;
let lastUpdate = null;
let highlightThreshold = 2100;

// Toast
function showToast(message, isError = false) {
  const container = document.getElementById('toast-container');
  if (!container) {
    console.error('Toast container not found!');
    return;
  }
  const toast = document.createElement('div');
  toast.classList.add('toast');
  if (isError) toast.classList.add('error');
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 3000);
}

// Copy LN Address
function copyText(element) {
  const address = element.getAttribute('data-address').trim();
  navigator.clipboard.writeText(address)
    .then(() => showToast('Lightning Address copied to clipboard!'))
    .catch(err => {
      console.error('Error copying Lightning Address:', err);
      showToast('Failed to copy Lightning Address.', true);
    });
}

// Copy LNURL
function copyLnurl(element) {
  const lnurl = element.getAttribute('data-lnurl');
  if (lnurl) {
    navigator.clipboard.writeText(lnurl)
      .then(() => showToast('LNURL copied to clipboard!'))
      .catch(err => {
        console.error('Error copying LNURL:', err);
        showToast('Failed to copy LNURL.', true);
      });
  } else {
    console.error('LNURL not found.');
    showToast('LNURL not found!', true);
  }
}

// Format date
function formatDate(dateString) {
  const now = new Date();
  const d = new Date(dateString);
  if (now.toDateString() === d.toDateString()) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
  } else {
    return d.toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' });
  }
}

// Update from fetched data
function updateDonations(data) {
  totalDonations = data.total_donations;
  document.getElementById('totalDonations').textContent = `${totalDonations} Sats`;

  if (data.donations.length > 0) {
    const latest = data.donations[data.donations.length - 1];
    document.getElementById('donationHistory').textContent =
      `Latest Entry: ${latest.amount} Sats - "${latest.memo}"`;
  } else {
    document.getElementById('donationHistory').textContent = 'Latest Entry: None yet.';
  }

  transactionsData = data.donations;
  updateLightningAddress(data.lightning_address, data.lnurl);

  if (data.highlight_threshold) {
    highlightThreshold = data.highlight_threshold;
  }

  renderTable();
  renderPagination();
}

// Update LN address
function updateLightningAddress(lightningAddress, lnurl) {
  const copyField = document.getElementById('lightning-address-container');
  const addressSpan = document.getElementById('lightning-address');

  if (copyField && addressSpan) {
    if (lightningAddress && lightningAddress !== 'Unavailable') {
      copyField.setAttribute('data-address', lightningAddress);
      addressSpan.textContent = lightningAddress;
    } else {
      copyField.setAttribute('data-address', 'Unknown Lightning Address');
      addressSpan.textContent = 'Unknown Lightning Address';
    }

    if (lnurl && lnurl !== 'Unavailable') {
      copyField.setAttribute('data-lnurl', lnurl);
    } else {
      copyField.setAttribute('data-lnurl', '');
    }
  } else {
    console.error('Lightning Address elements not found.');
  }
}

// Renders the guestbook table
function renderTable() {
  const tableBody = document.getElementById('transactions');
  tableBody.innerHTML = '';

  const startIndex = (currentPage - 1) * rowsPerPage;
  const endIndex = startIndex + rowsPerPage;
  // newest first
  const visible = transactionsData.slice().reverse().slice(startIndex, endIndex);

  if (!visible.length) {
    tableBody.innerHTML = '<tr><td colspan="4" class="no-data">No entries yet.</td></tr>';
  } else {
    visible.forEach(tx => {
      const row = document.createElement('tr');
      if (tx.amount > highlightThreshold) {
        row.classList.add('highlight');
      }
      row.setAttribute('data-id', tx.id);

      row.innerHTML = `
        <td>${formatDate(tx.date)}</td>
        <td>${tx.memo}</td>
        <td>${tx.amount} Sats</td>
        <td class="actions">
          <span class="like-button" onclick="voteDonation('${tx.id}', 'like')">
            <i class="material-icons">thumb_up</i>
            <span class="likes-count">${tx.likes}</span>
          </span>
          <span class="dislike-button" onclick="voteDonation('${tx.id}', 'dislike')">
            <i class="material-icons">thumb_down</i>
            <span class="dislikes-count">${tx.dislikes}</span>
          </span>
        </td>
      `;
      tableBody.appendChild(row);
    });
  }
}

// Pagination
function renderPagination() {
  const pagination = document.getElementById('pagination');
  pagination.innerHTML = '';
  const totalPages = Math.ceil(transactionsData.length / rowsPerPage);

  for (let i = 1; i <= totalPages; i++) {
    const a = document.createElement('a');
    a.textContent = i;
    a.href = '#';
    if (i === currentPage) {
      a.classList.add('active');
    }
    a.addEventListener('click', (e) => {
      e.preventDefault();
      currentPage = i;
      renderTable();
      renderPagination();
    });
    pagination.appendChild(a);
  }
}

/* Initial fetching */
async function fetchInitialDonations() {
  try {
    const [donationsResp, updatesResp] = await Promise.all([
      fetch('/api/donations'),
      fetch('/donations_updates')
    ]);

    if (!donationsResp.ok || !updatesResp.ok) {
      throw new Error('Failed to fetch initial data');
    }

    const donationsData = await donationsResp.json();
    const updatesData = await updatesResp.json();

    updateDonations(donationsData);
    lastUpdate = new Date(updatesData.last_update);

  } catch (error) {
    console.error('Error fetching initial donations:', error);
    showToast('Error fetching initial donations.', true);
  }
}

/* Polling for updates */
async function checkForUpdates() {
  try {
    const response = await fetch('/donations_updates');
    if (!response.ok) {
      throw new Error('Failed to fetch updates');
    }
    const data = await response.json();
    const serverUpdate = new Date(data.last_update);

    if (!lastUpdate || serverUpdate > lastUpdate) {
      lastUpdate = serverUpdate;
      const donationsResp = await fetch('/api/donations');
      if (!donationsResp.ok) {
        throw new Error('Failed to fetch updated donations');
      }
      const donationsData = await donationsResp.json();
      updateDonations(donationsData);
    }
  } catch (error) {
    console.error('Error checking for updates:', error);
    showToast('Error checking for updates.', true);
  } finally {
    setTimeout(checkForUpdates, 5000);
  }
}

// Voting
async function voteDonation(donationId, voteType) {
  try {
    const response = await fetch('/api/vote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ donation_id: donationId, vote_type: voteType })
    });
    const result = await response.json();

    if (response.ok) {
      const row = document.querySelector(`tr[data-id="${donationId}"]`);
      if (row) {
        if (voteType === 'like') {
          const likesEl = row.querySelector('.likes-count');
          likesEl.textContent = result.likes;
          showToast('You liked this entry!');
        } else {
          const dislikesEl = row.querySelector('.dislikes-count');
          dislikesEl.textContent = result.dislikes;
          showToast('You disliked this entry!');
        }
      } else {
        console.error(`Row with donationId ${donationId} not found.`);
        showToast('Entry not found.', true);
      }
    } else {
      showToast(result.error || 'Error processing your vote.', true);
    }

  } catch (error) {
    console.error('Error voting entry:', error);
    showToast('Error processing your vote.', true);
  }
}

/* Dark Mode */
function toggleDarkMode(isDark) {
  if (isDark) {
    document.body.classList.add('dark-mode');
    localStorage.setItem('darkMode', 'enabled');
  } else {
    document.body.classList.remove('dark-mode');
    localStorage.setItem('darkMode', 'disabled');
  }
}
function initializeDarkMode() {
  const toggle = document.getElementById('darkModeToggle');
  const saved = localStorage.getItem('darkMode');
  if (saved === 'enabled') {
    toggle.checked = true;
    document.body.classList.add('dark-mode');
  } else {
    toggle.checked = false;
    document.body.classList.remove('dark-mode');
  }
  toggle.addEventListener('change', (e) => {
    toggleDarkMode(e.target.checked);
  });
}

/* On DOM ready */
document.addEventListener('DOMContentLoaded', () => {
  initializeDarkMode();
  fetchInitialDonations();
  checkForUpdates();
});
