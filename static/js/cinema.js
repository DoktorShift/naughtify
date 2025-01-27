document.addEventListener('DOMContentLoaded', () => {
    const donationsData = [];
    let highlightThreshold = 2100;
    let isMuted = false;
    let lastKnownCount = 0;
    let userInteracted = false;

    // Sound files with preloading
    const normalSound = new Audio('/static/sounds/normal_payment.mp3');
    const bigSound = new Audio('/static/sounds/big_payment.mp3');

    normalSound.preload = 'auto';
    bigSound.preload = 'auto';

    // DOM Elements
    const totalElement = document.getElementById('cinema-total');
    const qrElement = document.getElementById('cinema-qr');
    const heroicPatronsList = document.getElementById('heroic-patrons-list');
    const transactionsBody = document.getElementById('cinema-transactions');
    const soundToggleBtn = document.getElementById('sound-toggle');
    const soundIcon = document.getElementById('sound-icon');
    const lightningAddressElement = document.getElementById('lightning-address');

    // Constant for number of rows to display
    const ROWS_TO_DISPLAY = 15;

    // Initialize the UI and start polling
    initialize();

    async function initialize() {
        console.log("Initializing donations dashboard.");
        await fetchData(); // Initial data fetch
        setInterval(fetchData, 10000); // Update UI every 10 seconds
        setInterval(checkNewDonations, 8000); // Check for new donations every 8 seconds

        // Event listener to capture user interaction for audio playback
        document.body.addEventListener('click', () => {
            if (!userInteracted) {
                userInteracted = true;
                console.log('User interacted with the page. Audio playback allowed.');
            }
        });
    }

    // Fetch data from API
    async function fetchData() {
        try {
            console.log("Fetching donations data from API.");
            const response = await fetch('/api/donations');
            if (!response.ok) {
                console.error('Failed to fetch donations data:', response.statusText);
                return;
            }
            const data = await response.json();
            console.log("Donations data fetched successfully:", data);
            updateUI(data);
        } catch (error) {
            console.error('Error fetching donations data:', error);
        }
    }

    // Update UI with data
    function updateUI(data) {
        console.log("Updating UI with fetched data.");
        totalElement.textContent = `${data.total_donations} Sats`;

        // Update QR code
        if (data.lnurl) {
            updateQR(data.lnurl);
        }

        // Update lightning address
        if (data.lightning_address) {
            lightningAddressElement.textContent = data.lightning_address;
            console.log("Lightning address updated:", data.lightning_address);
        }

        // Update highlight threshold
        if (typeof data.highlight_threshold === 'number') {
            highlightThreshold = data.highlight_threshold;
            console.log("Highlight threshold updated to:", highlightThreshold);
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
            console.log("Initial donations count set to:", lastKnownCount);
        }
    }

    // Update QR Code by reusing /donations HTML to get the same QR
    async function updateQR(lnurl) {
        try {
            console.log("Updating QR code.");
            const response = await fetch('/donations');
            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            const qrImg = doc.querySelector('.qr-card img');
            if (qrImg) {
                qrElement.src = qrImg.src;
                console.log("QR code updated successfully.");
            } else {
                console.error('QR code image not found in donations page.');
            }
        } catch (error) {
            console.error('Error updating QR code:', error);
        }
    }

    // Update Heroic Patrons
    function updateHeroicPatrons(donations) {
        console.log("Updating Heroic Patrons list.");
        heroicPatronsList.innerHTML = '';
        const sorted = [...donations].sort((a, b) => b.amount - a.amount);
        const topThree = sorted.slice(0, 3);
        topThree.forEach(patron => {
            const li = document.createElement('li');
            li.textContent = `${patron.amount} Sats - "${sanitizeHTML(patron.memo)}"`;
            heroicPatronsList.appendChild(li);
            console.log("Added patron:", patron);
        });
    }

    // Update Transactions Table (up to ROWS_TO_DISPLAY rows)
    function updateTransactions(donations) {
        console.log("Updating Transactions table.");
        transactionsBody.innerHTML = '';
        const rowsToShow = Math.min(donations.length, ROWS_TO_DISPLAY);
        const visible = donations.slice(0, rowsToShow);

        visible.forEach(donation => {
            const tr = document.createElement('tr');
            if (donation.amount > highlightThreshold) {
                tr.classList.add('highlight');
                console.log(`Donation amount ${donation.amount} exceeds highlight threshold.`);
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
            console.log("Added transaction row:", donation);
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
            console.log("Checking for new donations.");
            const response = await fetch('/api/donations');
            if (!response.ok) {
                console.error('Failed to fetch donations data for new donations check:', response.statusText);
                return;
            }
            const data = await response.json();
            console.log("Donations data fetched for new donations check:", data);

            const newCount = data.donations.length;
            if (newCount > lastKnownCount && lastKnownCount !== 0) {
                const newDonation = data.donations[newCount - 1];
                console.log('New donation detected:', newDonation);
                playSound(newDonation.amount);
            }
            lastKnownCount = newCount;
            console.log("Updated lastKnownCount to:", lastKnownCount);
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

        if (!userInteracted) {
            console.log('User has not interacted with the page yet. Cannot play sound.');
            return;
        }

        const sound = amount > highlightThreshold ? bigSound : normalSound;
        console.log('Attempting to play sound:', sound.src);

        sound.currentTime = 0; // Reset to start
        sound.play().then(() => {
            console.log('Sound played successfully:', sound.src);
        }).catch(err => {
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

    // ------------- NEW GO BACK FUNCTION -------------
    // This checks if there's any history to go back to;
    // if not, it redirects to '/', which you can replace
    // with any preferred fallback URL.

    window.goBack = function() {
  if (window.history.length > 1) {
    console.log('Going back one step in history.');
    window.history.back();
  } else {
    console.log('No browser history detected. Redirecting to base URL.');
    // Redirect to Base-URL.
    window.location.href = window.location.origin;
  }
};
