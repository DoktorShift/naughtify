let totalDonations = 0; // Total donations
let transactionsData = []; // Store transaction history
const rowsPerPage = 10; // Number of rows to display per page
let currentPage = 1;
let lastUpdate = null; // Timestamp of the last update

// Function to copy Lightning Address to clipboard
function copyText(element) {
    const text = element.querySelector('p').textContent.trim();
    navigator.clipboard.writeText(text).then(() => {
        alert('Lightning address copied to clipboard!');
    }).catch(err => {
        alert('Failed to copy!');
    });
}

// Function to copy LNURL to clipboard
function copyLnurl(element) {
    const lnurl = element.getAttribute('data-lnurl');
    if (lnurl) {
        navigator.clipboard.writeText(lnurl).then(() => {
            alert('LNURL copied to clipboard!');
        }).catch(err => {
            alert('Failed to copy LNURL!');
        });
    } else {
        alert('LNURL not found!');
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
    totalDonations = data.total_donations;
    document.getElementById('totalDonations').textContent = `${totalDonations} Sats`;

    // Update latest donation
    if (data.donations.length > 0) {
        const latestDonation = data.donations[data.donations.length - 1];
        document.getElementById('donationHistory').textContent = `Latest donation: ${latestDonation.amount} Sats - "${latestDonation.memo}"`;
    } else {
        document.getElementById('donationHistory').textContent = 'Latest donation: None yet.';
    }

    // Update transactions data
    transactionsData = data.donations;

    // Render the table and pagination
    renderTable();
    renderPagination();
}

// Function to render the transaction table
function renderTable() {
    const tableBody = document.getElementById('transactions');
    tableBody.innerHTML = '';

    const startIndex = (currentPage - 1) * rowsPerPage;
    const endIndex = startIndex + rowsPerPage;
    const visibleTransactions = transactionsData.slice().reverse().slice(startIndex, endIndex);

    if (visibleTransactions.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="3" class="no-data">No transactions yet.</td></tr>';
    } else {
        visibleTransactions.forEach((transaction) => {
            const row = document.createElement('tr');

            // Check if donation is greater than highlight threshold
            if (transaction.amount > 10000) { // Example Threshold: 10,000 Sats
                row.classList.add('highlight');
            }

            row.innerHTML = `
                <td>${formatDate(transaction.date)}</td>
                <td>${transaction.memo}</td>
                <td>${transaction.amount} Sats</td>
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
            pageLink.style.backgroundColor = 'var(--secondary-color)';
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
    } finally {
        // Schedule the next update check
        setTimeout(checkForUpdates, 5000); // Every 5 seconds
    }
}

// Initialize on page load
document.addEventListener("DOMContentLoaded", function() {
    // Fetch initial donations data
    fetchInitialDonations();
    // Start checking for updates
    checkForUpdates();
});
