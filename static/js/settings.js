// static/js/settings.js

/**
 * Sends a POST request to restart the server.
 * This function should be secured on the backend to prevent unauthorized access.
 */
function restartServer() {
    if (confirm("Do you really want to restart the server?")) {
        fetch('/admin/restart', { // Updated route under Admin Blueprint
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ action: 'restart' }) // Optional payload
        })
        .then(response => {
            if (response.redirected) {
                window.location.href = response.url;
            } else if (response.ok) {
                alert('Server is restarting...');
            } else {
                return response.json().then(data => {
                    throw new Error(data.error || 'Unknown error');
                });
            }
        })
        .catch(error => {
            console.error('Error restarting server:', error);
            alert('Error restarting the server: ' + error.message);
        });
}

/**
 * Toggles Dark Mode on the settings page.
 * @param {boolean} isDark - Indicates whether to enable dark mode.
 */
function toggleDarkMode(isDark) {
    if (isDark) {
        document.body.classList.add('dark-mode');
        localStorage.setItem('darkMode', 'enabled');
    } else {
        document.body.classList.remove('dark-mode');
        localStorage.setItem('darkMode', 'disabled');
    }
}

/**
 * Initializes Dark Mode based on user's preference stored in localStorage.
 * Also handles the toggle switch functionality.
 */
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

/**
 * Toggles password visibility for password input fields.
 * Adds event listeners to all buttons with the class 'toggle-password'.
 */
function togglePasswordVisibility() {
    const togglePasswordButtons = document.querySelectorAll('.toggle-password');
    togglePasswordButtons.forEach(button => {
        button.addEventListener('click', () => {
            const input = button.parentElement.previousElementSibling;
            if (input.type === 'password') {
                input.type = 'text';
                button.innerHTML = '<i class="fas fa-eye-slash"></i>';
            } else {
                input.type = 'password';
                button.innerHTML = '<i class="fas fa-eye"></i>';
            }
        });
    });
}

/**
 * Initializes event listeners when the DOM is fully loaded.
 */
document.addEventListener('DOMContentLoaded', () => {
    initializeDarkMode();          // Initialize Dark Mode toggle
    togglePasswordVisibility();    // Initialize password visibility toggles
});
