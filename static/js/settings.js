// static/js/settings.js

// Function to restart the server
function restartServer() {
    if (confirm("Do you really want to restart the server?")) {
        fetch('/restart', {
            method: 'POST'
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

// Dark Mode Toggle Script and Toggle Password Visibility
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

    // Toggle Password Visibility
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
});
