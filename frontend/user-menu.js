/*
  The account menu at the top right, beside Settings: who is signed in, and what they can do.

  The button shows the person's initials; the panel shows their name, email,
  role and base region, then Add person (Base head only) and Sign out.

  login.json has no name field, so the name is made from the email:
  "yogendra@..." -> "Yogendra", "ravi.kumar@..." -> "Ravi Kumar".

  app.js calls renderUserMenu() once /auth/me has answered. Add person's own
  form lives in users.js; this file only shows its menu item.
*/

const userMenu = {
    root: document.getElementById('user-menu'),
    button: document.getElementById('user-menu-button'),
    panel: document.getElementById('user-menu-panel'),
};


function displayNameFromEmail(email) {
    const local = String(email).split('@')[0];

    return local
        .split(/[._-]+/)
        .filter(Boolean)
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ') || email;
}


function initialsOf(name) {
    const words = name.split(' ').filter(Boolean);
    const letters = words.length > 1 ? words[0][0] + words[words.length - 1][0] : name.slice(0, 2);

    return letters.toUpperCase();
}


function renderUserMenu(user, { canAddPerson, logoutHref }) {
    const name = displayNameFromEmail(user.email);
    const initials = initialsOf(name);
    const role = user.role.replaceAll('_', ' ');

    for (const avatar of userMenu.root.querySelectorAll('.user-avatar')) {
        avatar.textContent = initials;
    }

    document.getElementById('user-menu-name').textContent = name;
    document.getElementById('user-menu-email').textContent = user.email;
    document.getElementById('user-menu-role').textContent = role;
    document.getElementById('user-menu-region').textContent = user.base_region || 'Not set';
    document.getElementById('user-menu-region').classList.toggle('is-empty', !user.base_region);

    document.getElementById('add-person-open').hidden = !canAddPerson;
    document.getElementById('user-logout').href = logoutHref;

    userMenu.button.title = name + ' — ' + role;
    userMenu.root.hidden = false;
}


function setUserMenuOpen(open) {
    userMenu.panel.hidden = !open;
    userMenu.button.setAttribute('aria-expanded', String(open));
}


userMenu.button.addEventListener('click', () => {
    setUserMenuOpen(userMenu.panel.hidden);
});

// Any item chosen closes the menu - Add person opens its own dialog.
userMenu.panel.addEventListener('click', (event) => {
    if (event.target.closest('.user-menu-item')) {
        setUserMenuOpen(false);
    }
});

document.addEventListener('click', (event) => {
    if (!userMenu.panel.hidden && !userMenu.root.contains(event.target)) {
        setUserMenuOpen(false);
    }
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !userMenu.panel.hidden) {
        setUserMenuOpen(false);
        userMenu.button.focus();
    }
});
