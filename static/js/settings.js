// static/js/settings.js

// Function to restart the server
function restartServer() {
    if (confirm("Do you really want to restart the server?")) {
        fetch('/restart', {
            method: 'POST'
        })
        .then(response => response.json())
        .then(data => {
            // Handle response if needed
            alert('Server is restarting...');
        })
        .catch(error => {
            console.error('Error restarting server:', error);
            alert('Error restarting the server.');
        });
}

// Dark Mode Toggle Script
document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('darkModeToggle');
    const body = document.body;

    // Initialize dark mode based on local storage
    if (localStorage.getItem('darkMode') === 'enabled') {
        body.classList.add('dark-mode');
        toggle.checked = true;
    }

    toggle.addEventListener('change', () => {
        if (toggle.checked) {
            body.classList.add('dark-mode');
            localStorage.setItem('darkMode', 'enabled');
        } else {
            body.classList.remove('dark-mode');
            localStorage.setItem('darkMode', 'disabled');
        }
    });
});
