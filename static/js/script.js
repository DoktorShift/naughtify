let totalDonations = 0; 
let transactionsData = [];
const rowsPerPage = 17; 
let currentPage = 1;
let lastUpdate = null; 
let highlightThreshold = 2100; 

function showToast(message, isError = false) {
    const toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        console.error('Toast container not found!');
        return;
    }

    const toast = document.createElement('div');
    toast.classList.add('toast');
    if (isError) {
        toast.classList.add('error');
    }
    toast.textContent = message;

    toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 3000);
}

function copyText(element) {
    const address = element.getAttribute('data-address').trim();
    navigator.clipboard.writeText(address).then(() => {
        showToast('Lightning Address copied to clipboard!');
    }).catch(err => {
        console.error('Error copying Lightning Address:', err);
        showToast('Failed to copy Lightning Address.', true);
    });
}

function copyLnurl(element) {
    const lnurl = element.getAttribute('data-lnurl');
    if (lnurl) {
        navigator.clipboard.writeText(lnurl).then(() => {
            showToast('LNURL copied to clipboard!');
        }).catch(err => {
            console.error('Error copying LNURL:', err);
            showToast('Failed to copy LNURL.', true);
        });
    } else {
        console.error('LNURL not found.');
        showToast('LNURL not found!', true);
    }
}

function formatDate(dateString) {
    const currentDate = new Date();
    const givenDate = new Date(dateString);

    if (currentDate.toDateString() === givenDate.toDateString()) {
        return givenDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
    } else {
        return givenDate.toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' });
    }
}

function updateDonations(data) {
    totalDonations = data.total_donations;
    document.getElementById('totalDonations').textContent = `${totalDonations} Sats`;

    if (data.donations.length > 0) {
        const latestDonation = data.donations[data.donations.length - 1];
        document.getElementById('donationHistory').textContent = `Latest Patron: ${latestDonation.amount} Sats - "${latestDonation.memo}"`;
    } else {
        document.getElementById('donationHistory').textContent = 'Latest Patron: None yet.';
    }

    transactionsData = data.donations;
    updateLightningAddress(data.lightning_address, data.lnurl);

    if (data.highlight_threshold) {
        highlightThreshold = data.highlight_threshold;
    }

    renderTable();
    renderPagination();
}

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

function renderTable() {
    const tableBody = document.getElementById('transactions');
    tableBody.innerHTML = '';

    const startIndex = (currentPage - 1) * rowsPerPage;
    const endIndex = startIndex + rowsPerPage;
    const visibleTransactions = transactionsData.slice().reverse().slice(startIndex, endIndex);

    if (visibleTransactions.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="4" class="no-data">No donors yet.</td></tr>';
    } else {
        visibleTransactions.forEach((transaction) => {
            const row = document.createElement('tr');
            if (transaction.amount > highlightThreshold) {
                row.classList.add('highlight');
            }

            row.setAttribute('data-id', transaction.id);

            row.innerHTML = `
                <td>${formatDate(transaction.date)}</td>
                <td>${transaction.memo}</td>
                <td>${transaction.amount} Sats</td>
                <td class="actions">
                    <span class="like-button" onclick="voteDonation('${transaction.id}', 'like')">
                        <i class="material-icons">thumb_up</i> <span class="likes-count">${transaction.likes}</span>
                    </span>
                    <span class="dislike-button" onclick="voteDonation('${transaction.id}', 'dislike')">
                        <i class="material-icons">thumb_down</i> <span class="dislikes-count">${transaction.dislikes}</span>
                    </span>
                </td>
            `;
            tableBody.appendChild(row);
        });
    }
}

function renderPagination() {
    const pagination = document.getElementById('pagination');
    pagination.innerHTML = '';
    const totalPages = Math.ceil(transactionsData.length / rowsPerPage);

    for (let i = 1; i <= totalPages; i++) {
        const pageLink = document.createElement('a');
        pageLink.textContent = i;
        pageLink.href = '#';
        if (i === currentPage) {
            pageLink.classList.add('active');
        }
        pageLink.addEventListener('click', (e) => {
            e.preventDefault();
            currentPage = i;
            renderTable();
            renderPagination();
        });
        pagination.appendChild(pageLink);
    }
}

async function fetchInitialDonations() {
    try {
        const [donationsResponse, updatesResponse] = await Promise.all([
            fetch('/api/donations'),
            fetch('/donations_updates')
        ]);

        if (!donationsResponse.ok || !updatesResponse.ok) {
            throw new Error('Failed to fetch initial data');
        }

        const donationsData = await donationsResponse.json();
        const updatesData = await updatesResponse.json();

        updateDonations(donationsData);
        lastUpdate = new Date(updatesData.last_update);

    } catch (error) {
        console.error('Error fetching initial donations:', error);
        showToast('Error fetching initial donations.', true);
    }
}

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
            const donationsResponse = await fetch('/api/donations');
            if (!donationsResponse.ok) {
                throw new Error('Failed to fetch updated donations');
            }
            const donationsData = await donationsResponse.json();
            updateDonations(donationsData);
        }

    } catch (error) {
        console.error('Error checking for updates:', error);
        showToast('Error checking for updates.', true);
    } finally {
        setTimeout(checkForUpdates, 5000);
    }
}

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
                    const likesCell = row.querySelector('.likes-count');
                    likesCell.textContent = result.likes;
                    showToast('You liked this donation!');
                } else if (voteType === 'dislike') {
                    const dislikesCell = row.querySelector('.dislikes-count');
                    dislikesCell.textContent = result.dislikes;
                    showToast('You disliked this donation!');
                }
            } else {
                console.error(`Row with donationId ${donationId} not found.`);
                showToast('Donation not found.', true);
            }
        } else {
            showToast(result.error || 'Error processing your vote.', true);
        }

    } catch (error) {
        console.error('Error voting donation:', error);
        showToast('Error processing your vote.', true);
    }
}

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
    const darkModeToggle = document.getElementById('darkModeToggle');
    const darkModeSetting = localStorage.getItem('darkMode');

    if (darkModeSetting === 'enabled') {
        darkModeToggle.checked = true;
        document.body.classList.add('dark-mode');
    } else {
        darkModeToggle.checked = false;
        document.body.classList.remove('dark-mode');
    }

    darkModeToggle.addEventListener('change', (e) => {
        toggleDarkMode(e.target.checked);
    });
}

document.addEventListener("DOMContentLoaded", function() {
    initializeDarkMode();
    fetchInitialDonations();
    checkForUpdates();
});
