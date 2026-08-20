(function () {
  var EMPTY_HTML = '<p class="catalog-empty">No prints in the catalog yet.</p>';

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderCatalogCards(items) {
    if (!items || items.length === 0) {
      return EMPTY_HTML;
    }
    return items
      .map(function (item) {
        var note = item.note ? '<p class="catalog-note">' + escapeHtml(item.note) + "</p>" : "";
        return (
          '<article class="catalog-card">' +
          '<img src="' +
          escapeHtml(item.image) +
          '" alt="' +
          escapeHtml(item.title) +
          '">' +
          '<h2 class="catalog-title">' +
          escapeHtml(item.title) +
          "</h2>" +
          note +
          '<time class="catalog-added" datetime="' +
          escapeHtml(item.added) +
          '">Added ' +
          escapeHtml(item.added) +
          "</time>" +
          "</article>"
        );
      })
      .join("");
  }

  function paint(root, items) {
    var empty = !items || items.length === 0;
    root.dataset.state = empty ? "empty" : "ready";
    root.innerHTML = renderCatalogCards(items);
  }

  function init(root, itemsUrl) {
    var target = root || document.getElementById("catalog-root");
    if (!target) {
      return Promise.resolve([]);
    }
    return fetch(itemsUrl || "catalog/items.json")
      .then(function (res) {
        if (!res.ok) {
          throw new Error("catalog unavailable");
        }
        return res.json();
      })
      .then(function (items) {
        paint(target, Array.isArray(items) ? items : []);
        return items;
      })
      .catch(function () {
        paint(target, []);
        return [];
      });
  }

  window.MiKraftsCatalog = {
    renderCatalogCards: renderCatalogCards,
    init: init,
  };

  if (document.getElementById("catalog-root")) {
    init();
  }
})();
