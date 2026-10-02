/* The sign-in page: post the email and password, then go to the dashboard. */

const $ = (id) => document.getElementById(id);

function say(box, message) {
    box.textContent = message || '';
    box.hidden = !message;
}

/* ---- sign in ---- */
$('login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    say($('error'), '');
    $('submit').disabled = true;

    try {
        const res = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: $('email').value, password: $('password').value }),
        });

        if (res.ok) {
            location.href = '/';
            return;
        }
        const body = await res.json().catch(() => ({}));
        say($('error'), body.detail || 'Sign-in failed.');
        $('password').value = '';
    } finally {
        $('submit').disabled = false;
    }
});
