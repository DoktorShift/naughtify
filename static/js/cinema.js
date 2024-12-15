document.addEventListener('DOMContentLoaded', () => {
    const donationsData = [];
    let highlightThreshold = 2100;
    let isMuted = false;
    let lastKnownCount = 0;

    // Sound files with preloading
    const normalSound = new Audio('static/sounds/normal_payment.mp3');
    const bigSound = new Audio('static/sounds/big_payment.mp3');

    normalSound.preload = 'auto';
    bigSound.preload = 'auto';

    // DOM Elements
    const totalElement = document.getElementById('cinema-total');
    const qrElement = document.getElementById('cinema-qr');
    const heroicPatronsList = document.getElementById('heroic-patrons-list');
    const transactionsBody = document.getElementById('cinema-transactions');
    const soundToggleBtn = document.getElementById('sound-toggle');
    const soundIcon = document.getElementById('sound-icon');
    const lightningAddressElement = document.getElementById('lightning-address'); // New element

    // Constant for number of rows to display
    const ROWS_TO_DISPLAY = 15;

    // Initialize the UI and start polling
    initialize();

    async function initialize() {
        await fetchData(); // Initial data fetch
        setInterval(fetchData, 10000); // Update UI every 10 seconds
        setInterval(checkNewDonations, 8000); // Check for new donations every 8 seconds
    }

    // Fetch data from API
    async function fetchData() {
        try {
            const response = await fetch('/api/donations');
            if (!response.ok) {
                console.error('Failed to fetch donations data:', response.statusText);
                return;
            }
            const data = await response.json();
            updateUI(data);
        } catch (error) {
            console.error('Error fetching donations data:', error);
        }
    }

    // Update UI with data
    function updateUI(data) {
        totalElement.textContent = `${data.total_donations} Sats`;

        // Update QR code
        if (data.lnurl) {
            updateQR(data.lnurl);
        }

        // Update lightning address
        if (data.lightning_address) {
            lightningAddressElement.textContent = data.lightning_address;
        }

        // Update highlight threshold
        if (typeof data.highlight_threshold === 'number') {
            highlightThreshold = data.highlight_threshold;
        }

        // Update donations data
        donationsData.length = 0; // Clear existing data
        donationsData.push(...(data.donations || []));
        donationsData.sort((a, b) => new Date(b.date) - new Date(a.date));

        updateHeroicPatrons(donationsData);
        updateTransactions(donationsData);

        // Initialize lastKnownCount if it's the first run
        if (lastKnownCount === 0) {
            lastKnownCount = donationsData.length;
        }
    }

    // Update QR Code by reusing /donations HTML to get the same QR
    async function updateQR(lnurl) {
        try {
            const response = await fetch('/donations');
            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            const qrImg = doc.querySelector('.qr-card img');
            if (qrImg) {
                qrElement.src = qrImg.src;
            }
        } catch (error) {
            console.error('Error updating QR code:', error);
        }
    }

    // Update Heroic Patrons
    function updateHeroicPatrons(donations) {
        heroicPatronsList.innerHTML = '';
        const sorted = [...donations].sort((a, b) => b.amount - a.amount);
        const topThree = sorted.slice(0, 3);
        topThree.forEach(patron => {
            const li = document.createElement('li');
            li.textContent = `${patron.amount} Sats - "${sanitizeHTML(patron.memo)}"`;
            heroicPatronsList.appendChild(li);
        });
    }

    // Update Transactions Table (up to ROWS_TO_DISPLAY rows)
    function updateTransactions(donations) {
        transactionsBody.innerHTML = '';
        const rowsToShow = Math.min(donations.length, ROWS_TO_DISPLAY);
        const visible = donations.slice(0, rowsToShow);

        visible.forEach(donation => {
            const tr = document.createElement('tr');
            if (donation.amount > highlightThreshold) {
                tr.classList.add('highlight');
            }

            const date = new Date(donation.date);
            const formattedTime = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });

            tr.innerHTML = `
                <td>${formattedTime}</td>
                <td>${sanitizeHTML(donation.memo)}</td>
                <td>${donation.amount} Sats</td>
                <td>${donation.likes - donation.dislikes}</td>
            `;
            transactionsBody.appendChild(tr);
        });
    }

    // Sanitize HTML to prevent XSS
    function sanitizeHTML(str) {
        const temp = document.createElement('div');
        temp.textContent = str;
        return temp.innerHTML;
    }

    // Check for new donations to play sound
    async function checkNewDonations() {
        try {
            const response = await fetch('/api/donations');
            if (!response.ok) {
                console.error('Failed to fetch donations data for new donations check:', response.statusText);
                return;
            }
            const data = await response.json();

            const newCount = data.donations.length;
            if (newCount > lastKnownCount && lastKnownCount !== 0) {
                const newDonation = data.donations[newCount - 1];
                console.log('New donation detected:', newDonation);
                playSound(newDonation.amount);
            }
            lastKnownCount = newCount;
        } catch (error) {
            console.error('Error checking for new donations:', error);
        }
    }

    // Play sound based on donation amount
    function playSound(amount) {
        if (isMuted) {
            console.log('Sounds are muted. Skipping playback.');
            return;
        }

        const sound = amount > highlightThreshold ? bigSound : normalSound;
        console.log('Playing sound:', sound.src);
        sound.currentTime = 0; // Reset to start
        sound.play().catch(err => {
            console.error('Audio playback failed:', err);
        });
    }

    // Sound toggle functionality
    soundToggleBtn.addEventListener('click', () => {
        isMuted = !isMuted;
        soundIcon.textContent = isMuted ? '🔇' : '🔊';
        soundToggleBtn.setAttribute('aria-label', isMuted ? 'Unmute Sounds' : 'Mute Sounds');
        console.log('Sound toggled. isMuted:', isMuted);
    });

    // Accessibility: Initialize aria-label
    soundToggleBtn.setAttribute('aria-label', 'Mute Sounds');
});
