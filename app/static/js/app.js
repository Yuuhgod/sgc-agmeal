(function () {
    "use strict";

    // Fecha os alertas (exceto .alert-secondary, usado como "nota informativa") depois de 4s.
    function autoFecharAlertas() {
        if (typeof bootstrap === "undefined") return;
        setTimeout(function () {
            document
                .querySelectorAll(".alert:not(.alert-secondary):not(.no-auto-close)")
                .forEach(function (alerta) {
                    try {
                        bootstrap.Alert.getOrCreateInstance(alerta).close();
                    } catch (e) {
                        /* silencioso */
                    }
                });
        }, 4000);
    }

    function mascaraCPF(campo) {
        let valor = campo.value.replace(/\D/g, "").slice(0, 11);
        valor = valor.replace(/(\d{3})(\d)/, "$1.$2");
        valor = valor.replace(/(\d{3})(\d)/, "$1.$2");
        valor = valor.replace(/(\d{3})(\d{1,2})$/, "$1-$2");
        campo.value = valor;
    }

    function mascaraTelefone(campo) {
        let valor = campo.value.replace(/\D/g, "").slice(0, 11);
        valor = valor.replace(/^(\d{2})(\d)/g, "($1) $2");
        valor = valor.replace(/(\d)(\d{4})$/, "$1-$2");
        campo.value = valor;
    }

    // Expõe globalmente para continuar suportando onchange/oninput nos templates.
    window.mascaraCPF = mascaraCPF;
    window.mascaraTelefone = mascaraTelefone;

    /**
     * Formulários com classe `form-submit-loading`: ao submeter, desactiva o botão
     * e mostra spinner (PDF, backup e outros pedidos lentos). Reactiva após timeout
     * de segurança (ex.: PDF em target=_blank não recarrega a página).
     */
    function initFormSubmitLoading() {
        document.querySelectorAll("form.form-submit-loading").forEach(function (form) {
            form.addEventListener("submit", function () {
                var btn = form.querySelector('button[type="submit"]');
                if (!btn || btn.disabled) {
                    return;
                }
                btn.disabled = true;
                var label = form.getAttribute("data-loading-label") || "Processando…";
                var orig = btn.innerHTML;
                btn.setAttribute("data-original-html", orig);
                btn.innerHTML =
                    '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' +
                    label;
                window.setTimeout(function () {
                    btn.disabled = false;
                    btn.innerHTML = btn.getAttribute("data-original-html") || orig;
                }, 120000);
            });
        });
    }

    /**
     * Links `a.pdf-link-loading` que abrem PDF noutro separador: feedback visual e evita cliques repetidos.
     */
    function initPdfLinkLoading() {
        document.querySelectorAll("a.pdf-link-loading[target='_blank']").forEach(function (a) {
            a.addEventListener("click", function () {
                if (a.getAttribute("data-pdf-loading") === "1") {
                    return;
                }
                a.setAttribute("data-pdf-loading", "1");
                a.classList.add("disabled", "pe-none");
                var label = a.getAttribute("data-loading-label") || "";
                var orig = a.innerHTML;
                a.setAttribute("data-original-html", orig);
                a.innerHTML =
                    '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>' +
                    (label ? ' <span class="small">' + label + "</span>" : "");
                window.setTimeout(function () {
                    a.innerHTML = a.getAttribute("data-original-html") || orig;
                    a.classList.remove("disabled", "pe-none");
                    a.removeAttribute("data-pdf-loading");
                }, 90000);
            });
        });
    }

    /**
     * Editor de dependentes (`[data-editor-dependentes]`): adiciona linhas a partir do
     * <template> e remove a linha do botão clicado.
     */
    function initEditorDependentes() {
        document.querySelectorAll("[data-editor-dependentes]").forEach(function (editor) {
            var lista = editor.querySelector("[data-lista-dependentes]");
            var modelo = editor.querySelector("template[data-modelo-dependente]");
            var vazio = editor.querySelector("[data-sem-dependentes]");
            function atualizarVazio() {
                if (vazio) vazio.hidden = lista.children.length > 0;
            }
            editor.querySelector("[data-adicionar-dependente]").addEventListener("click", function () {
                lista.appendChild(modelo.content.cloneNode(true));
                var linhas = lista.querySelectorAll(".linha-dependente");
                linhas[linhas.length - 1].querySelector('input[name="dep_nome"]').focus();
                atualizarVazio();
            });
            lista.addEventListener("click", function (ev) {
                var botao = ev.target.closest("[data-remover-dependente]");
                if (!botao) return;
                botao.closest(".linha-dependente").remove();
                atualizarVazio();
            });
            atualizarVazio();
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        autoFecharAlertas();
        initFormSubmitLoading();
        initPdfLinkLoading();
        initEditorDependentes();
    });
})();
