(() => {
  const body = document.getElementById('body');
  if (!body) return;
  const form = body.form;
  const format = document.getElementById('body-format');
  const status = document.getElementById('editor-status');
  const recipients = [...form.querySelectorAll('input[name="recipients"]')];
  const all = document.getElementById('select-all');
  if (all) {
    document.getElementById('select-all-row').hidden = false;
    const update = () => {
      const count = recipients.filter(input => input.checked).length;
      all.checked = count === recipients.length;
      all.indeterminate = count > 0 && count < recipients.length;
      document.getElementById('recipient-count').textContent = `Zaznaczono ${count} z ${recipients.length} adresów (limit: 500).`;
      all.setCustomValidity(count > 500 ? 'Możesz wysłać wiadomość do maksymalnie 500 adresów naraz.' : '');
    };
    all.addEventListener('change', () => {
      recipients.forEach(input => { input.checked = all.checked; });
      update();
    });
    recipients.forEach(input => input.addEventListener('change', update));
    update();
  }
  if (!window.tinymce) {
    status.textContent = 'Edytor nie został załadowany. Możesz użyć pola tekstowego.';
    return;
  }
  const originalBody = body.value;
  const originalFormat = format.value;
  let editor = null;
  let loading = true;
  const initialContent = originalFormat === 'html' ? originalBody : originalBody.split('\n').map(line => {
    const paragraph = document.createElement('p');
    paragraph.textContent = line;
    if (!line) paragraph.append(document.createElement('br'));
    return paragraph.outerHTML;
  }).join('');
  form.addEventListener('submit', event => {
    if (loading) {
      event.preventDefault();
      status.textContent = 'Poczekaj na uruchomienie edytora.';
      return;
    }
    if (!editor) return;
    editor.save();
    if ((!editor.getContent({ format: 'text' }).replace(/\u200b/g, '').trim() && !editor.getBody().querySelector('img[src]')) || body.value.length > 20000) {
      event.preventDefault();
      status.textContent = body.value.length > 20000 ? 'Treść wraz z formatowaniem przekracza limit 20 000 znaków.' : 'Podaj treść wiadomości.';
      editor.focus();
    }
  }, true);
  tinymce.init({
    target: body,
    base_url: '/static/vendor/tinymce',
    suffix: '.min',
    license_key: 'gpl',
    promotion: false,
    menubar: 'file edit view insert format tools table help',
    height: 680,
    min_height: 450,
    resize: true,
    plugins: 'advlist anchor autolink charmap code codesample directionality emoticons fullscreen help image insertdatetime link lists nonbreaking preview quickbars searchreplace table visualblocks visualchars wordcount',
    toolbar: [
      'undo redo | blocks fontfamily fontsize | bold italic underline strikethrough | forecolor backcolor | removeformat',
      'alignleft aligncenter alignright alignjustify | lineheight | bullist numlist outdent indent | subscript superscript | ltr rtl',
      'link unlink anchor image table | charmap emoticons insertdatetime codesample | searchreplace | code preview fullscreen help',
    ],
    toolbar_mode: 'wrap',
    // Match the server allowlist so supported formatting survives delivery.
    valid_elements: '@[style|title|dir|id],p,div,br,strong,b,em,i,u,s,strike,span,ul,ol[start|type],li[value],a[href|target|rel],blockquote,h1,h2,h3,h4,h5,h6,pre,code,sub,sup,hr,img[src|alt|width|height],table[width|height|border|cellpadding|cellspacing],caption,colgroup,col[span|width],thead,tbody,tfoot,tr,th[colspan|rowspan|scope|width|height],td[colspan|rowspan|width|height],figure,figcaption',
    quickbars_selection_toolbar: 'bold italic underline | quicklink blockquote',
    quickbars_insert_toolbar: 'quicktable hr',
    image_uploadtab: false,
    image_advtab: true,
    image_caption: true,
    automatic_uploads: false,
    object_resizing: 'img,table',
    browser_spellcheck: true,
    contextmenu: 'link image table',
    formats: { underline: { inline: 'u', exact: true } },
    link_target_list: false,
    link_title: false,
    link_default_protocol: 'https',
    convert_urls: false,
    paste_data_images: false,
    content_style: 'body { font-family: Arial, sans-serif; font-size: 16px; }',
    setup(instance) {
      instance.on('init', () => {
        editor = instance;
        editor.setContent(initialContent);
        format.value = 'html';
        body.required = false;
        editor.save();
        loading = false;
      });
      instance.on('change input undo redo', () => {
        if (editor) editor.save();
      });
    },
  }).catch(() => {
    tinymce.get('body')?.remove();
    editor = null;
    loading = false;
    body.hidden = false;
    body.style.display = '';
    body.required = true;
    body.value = originalBody;
    format.value = originalFormat;
    status.textContent = 'Nie udało się uruchomić edytora. Możesz użyć pola tekstowego.';
  });
})();
