'use strict';
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const value = button.dataset.copy;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
      } else {
        const field = document.createElement('textarea');
        field.value = value;
        field.className = 'clipboard-field';
        document.body.appendChild(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        if (!copied) throw new Error('copy');
      }
      button.textContent = 'Copiado ✓';
      setTimeout(() => { button.textContent = 'Copiar'; }, 2000);
    } catch {
      button.textContent = 'Selecciona el código';
    }
  });
});
