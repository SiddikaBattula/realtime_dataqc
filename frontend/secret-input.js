/*
  Password boxes the browser does not offer to save.

  Any <input type="password"> that is submitted makes the browser pop up
  "Save password?". Inputs marked data-secret are plain text boxes drawn as
  dots instead, which the password manager does not treat as a password.

  A browser that cannot draw text as dots gets type="password" back - the
  popup returns there, but the password is never shown in the clear.

  Used by login.html and the dashboard's Add person form.
*/

(function () {
    const masked = window.CSS && CSS.supports('-webkit-text-security', 'disc');

    for (const input of document.querySelectorAll('input[data-secret]')) {
        input.type = masked ? 'text' : 'password';
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.autocapitalize = 'off';
        input.classList.add('secret-input');
    }
})();
