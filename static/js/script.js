// script.js

let totalDonations = 0; // Total donations
let transactionsData = []; // Store transaction history
const rowsPerPage = 10; // Number of rows to display per page
let currentPage = 1;
let lastUpdate = null; // Timestamp of the last update
let highlightThreshold = 2100; // Default threshold

// Function to show a toast notification
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

    // Remove the toast after 3 seconds
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

// Function to copy Lightning Address to clipboard
function copyText(element) {
    // Extract the address from the data-address attribute
    const address = element.getAttribute('data-address').trim();
    console.log('Attempting to copy Lightning Address:', address); // Debugging
    navigator.clipboard.writeText(address).then(() => {
        console.log('Lightning Address copied successfully');
        showToast('Lightning Address copied to clipboard!');
    }).catch(err => {
        console.error('Error copying Lightning Address:', err);
        showToast('Failed to copy Lightning Address.', true);
    });
}

// Function to copy LNURL to clipboard
function copyLnurl(element) {
    const lnurl = element.getAttribute('data-lnurl');
    console.log('Attempting to copy LNURL:', lnurl); // Debugging
    if (lnurl) {
        navigator.clipboard.writeText(lnurl).then(() => {
            console.log('LNURL copied successfully');
            showToast('LNURL copied to clipboard!');
        }).catch(err => {
            console.error('Error copying LNURL:', err);
            showToast('Failed to copy LNURL.', true);
        });
    } else {
        console.error('LNURL not found in the clicked element.');
        showToast('LNURL not found!', true);
    }
}

// Function to format the date and time
function formatDate(dateString) {
    const currentDate = new Date();
    const givenDate = new Date(dateString);

    if (currentDate.toDateString() === givenDate.toDateString()) {
        // Today's donation: show only time
        return givenDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
    } else {
        // Older donations: show date only
        return givenDate.toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' });
    }
}

// Function to update the UI with new data
function updateDonations(data) {
    console.log('Updating donations with data:', data); // Debugging
    totalDonations = data.total_donations;
    document.getElementById('totalDonations').textContent = `${totalDonations} Sats`;

    // Update latest donation
    if (data.donations.length > 0) {
        const latestDonation = data.donations[data.donations.length - 1];
        document.getElementById('donationHistory').textContent = `Latest Patron: ${latestDonation.amount} Sats - "${latestDonation.memo}"`;
    } else {
        document.getElementById('donationHistory').textContent = 'Latest Patron: None yet.';
    }

    // Update transactions data
    transactionsData = data.donations;

    // Update Lightning Address and LNURL
    updateLightningAddress(data.lightning_address, data.lnurl);

    // Update Highlight Threshold
    if (data.highlight_threshold) {
        highlightThreshold = data.highlight_threshold;
        console.log(`Highlight Threshold updated to: ${highlightThreshold} sats`);
    }

    // Render the table and pagination
    renderTable();
    renderPagination();
}

// Function to update the Lightning Address and LNURL in the DOM
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
    } else {
        console.error('Lightning Address elements not found in the DOM.');
    }
}

// Function to render the donation table
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
            row.setAttribute('data-id', transaction.id); // Adding data-id attribute

            // Check if donation is greater than highlight threshold
            if (transaction.amount > highlightThreshold) { // Use dynamic threshold
                row.classList.add('highlight');
            }

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

// Function to render pagination
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

// Function to fetch initial donations data from the server
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

        // Update the UI with initial data
        updateDonations(donationsData);

        // Set the initial lastUpdate timestamp
        lastUpdate = new Date(updatesData.last_update);

    } catch (error) {
        console.error('Error fetching initial donations:', error);
        showToast('Error fetching initial donations.', true);
    }
}

// Function to check for updates using long-polling
async function checkForUpdates() {
    try {
        const response = await fetch('/donations_updates');
        if (!response.ok) {
            throw new Error('Failed to fetch updates');
        }

        const data = await response.json();
        const serverUpdate = new Date(data.last_update);

        if (!lastUpdate || serverUpdate > lastUpdate) {
            // New update detected
            lastUpdate = serverUpdate;
            // Fetch the latest donations data
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
        // Schedule the next update check
        setTimeout(checkForUpdates, 5000); // Every 5 seconds
    }
}

// Function to handle voting (like/dislike)
async function voteDonation(donationId, voteType) {
    try {
        const response = await fetch('/api/vote', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ donation_id: donationId, vote_type: voteType })
        });

        const result = await response.json();

        if (response.ok) {
            // Update the likes/dislikes counts in the table
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

// Helper function to get donation data by ID
function getDonationById(donationId) {
    return transactionsData.find(donation => donation.id === donationId);
}

// Function to toggle Dark Mode
function toggleDarkMode(isDark) {
    if (isDark) {
        document.body.classList.add('dark-mode');
        localStorage.setItem('darkMode', 'enabled');
    } else {
        document.body.classList.remove('dark-mode');
        localStorage.setItem('darkMode', 'disabled');
    }
}

// Function to initialize Dark Mode based on user preference
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

    // Add event listener for the toggle
    darkModeToggle.addEventListener('change', (e) => {
        toggleDarkMode(e.target.checked);
    });
}

// Modal Functionality (If Needed)
function openInfoModal(event) {
    event.stopPropagation(); // Prevent triggering other click events
    const modal = document.getElementById('infoModal');
    if (modal) {
        modal.style.display = 'block';
    }
}

function closeInfoModal() {
    const modal = document.getElementById('infoModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

// Close the modal when clicking outside the modal content
window.onclick = function(event) {
    const modal = document.getElementById('infoModal');
    if (modal && event.target == modal) {
        modal.style.display = 'none';
    }
}

// Initialize on page load
document.addEventListener("DOMContentLoaded", function() {
    // Initialize Dark Mode
    initializeDarkMode();
    
    // Fetch initial donations data
    fetchInitialDonations();
    // Start checking for updates
    checkForUpdates();
});
