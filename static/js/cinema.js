let donationsData = [];
let highlightThreshold = 2100;
let isMuted = false;

const normalSound = new Audio('static/sounds/normal_payment.mp3');
const bigSound = new Audio('static/sounds/big_payment.mp3');

const totalElement = document.getElementById('cinema-total');
const qrElement = document.getElementById('cinema-qr');
const heroicPatronsList = document.getElementById('heroic-patrons-list');
const transactionsBody = document.getElementById('cinema-transactions');
const soundToggleBtn = document.getElementById('sound-toggle');
const soundIcon = document.getElementById('sound-icon');

async function fetchData() {
    const response = await fetch('/api/donations');
    if (!response.ok) return;
    const data = await response.json();
    updateUI(data);
}

// Update UI with data
function updateUI(data) {
    totalElement.textContent = `${data.total_donations} Sats`;

    // Update QR code
    if (data.lnurl) {
        updateQR(data.lnurl);
    }

    // Update highlight threshold
    if (data.highlight_threshold) {
        highlightThreshold = data.highlight_threshold;
    }

    donationsData = data.donations || [];
    donationsData.sort((a, b) => new Date(b.date) - new Date(a.date));

    updateHeroicPatrons(donationsData);
    updateTransactions(donationsData);
}

// Update QR Code by reusing /donations HTML to get the same QR
function updateQR(lnurl) {
    fetch('/donations').then(response => response.text()).then(html => {
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const qrImg = doc.querySelector('.qr-card img');
        if (qrImg) {
            qrElement.src = qrImg.src;
        }
    });
}

// Update Heroic Patrons
function updateHeroicPatrons(donations) {
    heroicPatronsList.innerHTML = '';
    const sorted = [...donations].sort((a, b) => b.amount - a.amount);
    const topThree = sorted.slice(0, 3);
    topThree.forEach(patron => {
        const li = document.createElement('li');
        li.textContent = `${patron.amount} Sats - "${patron.memo}"`;
        heroicPatronsList.appendChild(li);
    });
}

// Update Transactions Table (up to 10 rows)
function updateTransactions(donations) {
    transactionsBody.innerHTML = '';
    const rowsToShow = Math.min(donations.length, 10);
    const visible = donations.slice(0, rowsToShow);

    visible.forEach(donation => {
        const tr = document.createElement('tr');
        if (donation.amount > highlightThreshold) {
            tr.classList.add('highlight');
        }

        const date = new Date(donation.date);
        const formattedTime = date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', hour12: true});

        tr.innerHTML = `
            <td>${formattedTime}</td>
            <td>${donation.memo}</td>
            <td>${donation.amount} Sats</td>
            <td>${donation.likes - donation.dislikes}</td>
        `;
        transactionsBody.appendChild(tr);
    });
}

// Check for new donations to play sound
let lastKnownCount = 0;
async function checkNewDonations() {
    const response = await fetch('/api/donations');
    if (!response.ok) return;
    const data = await response.json();

    const newCount = data.donations.length;
    if (newCount > lastKnownCount && lastKnownCount !== 0) {
        const newDonation = data.donations[data.donations.length - 1];
        playSound(newDonation.amount);
    }
    lastKnownCount = newCount;
}

function playSound(amount) {
    if (isMuted) return;
    if (amount > highlightThreshold) {
        bigSound.play();
    } else {
        normalSound.play();
    }
}

// Sound toggle
soundToggleBtn.addEventListener('click', () => {
    isMuted = !isMuted;
    soundIcon.textContent = isMuted ? '🔇' : '🔊';
});

fetchData();
setInterval(fetchData, 10000); // update UI every 10s
setInterval(checkNewDonations, 5000); // check for new donations every 5s
