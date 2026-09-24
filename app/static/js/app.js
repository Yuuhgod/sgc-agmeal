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

    /**
     * Formulários com `data-confirmar="mensagem"` pedem confirmação antes de enviar.
     * (A mensagem vem escapada pelo Jinja no atributo; sem JS inline com aspas frágeis.)
     */
    function initConfirmacoes() {
        document.addEventListener("submit", function (ev) {
            var form = ev.target;
            var msg = form.getAttribute && form.getAttribute("data-confirmar");
            if (msg && !window.confirm(msg)) {
                ev.preventDefault();
                ev.stopImmediatePropagation();
            }
        }, true);
    }

    /**
     * Campo de foto com recorte 3x4 (`[data-recorte-foto]`, macro campo_foto em _foto.html).
     * O recorte (300x400 JPEG) vai em foto_base64; o arquivo original não é enviado.
     */
    function initRecorteFoto() {
        if (typeof Cropper === "undefined" || typeof bootstrap === "undefined") return;
        document.querySelectorAll("[data-recorte-foto]").forEach(function (campo) {
            var arquivo = campo.querySelector("[data-foto-arquivo]");
            var base64 = campo.querySelector("[data-foto-base64]");
            var preview = campo.querySelector("[data-foto-preview]");
            var vazia = campo.querySelector("[data-foto-vazia]");
            var modalEl = campo.querySelector("[data-foto-modal]");
            var imagem = campo.querySelector("[data-foto-recortar]");
            var modal = new bootstrap.Modal(modalEl);
            var cropper = null;
            var confirmado = false;

            // Com JavaScript, só o recorte é enviado (o original pode ter vários MB).
            arquivo.removeAttribute("name");

            arquivo.addEventListener("change", function () {
                if (!arquivo.files || !arquivo.files.length) return;
                var leitor = new FileReader();
                leitor.onload = function (ev) {
                    imagem.src = ev.target.result;
                    confirmado = false;
                    modal.show();
                };
                leitor.readAsDataURL(arquivo.files[0]);
            });
            modalEl.addEventListener("shown.bs.modal", function () {
                cropper = new Cropper(imagem, { aspectRatio: 3 / 4, viewMode: 2, dragMode: "move" });
            });
            modalEl.addEventListener("hidden.bs.modal", function () {
                if (cropper) {
                    cropper.destroy();
                    cropper = null;
                }
                if (!confirmado) arquivo.value = "";
            });
            campo.querySelector("[data-foto-confirmar]").addEventListener("click", function () {
                var dados = cropper.getCroppedCanvas({ width: 300, height: 400 }).toDataURL("image/jpeg", 0.9);
                preview.src = dados;
                preview.classList.remove("d-none");
                vazia.classList.add("d-none");
                base64.value = dados;
                confirmado = true;
                modal.hide();
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        autoFecharAlertas();
        initFormSubmitLoading();
        initPdfLinkLoading();
        initEditorDependentes();
        initConfirmacoes();
        initRecorteFoto();
    });
})();
