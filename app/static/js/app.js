(function () {
    "use strict";

    // Fecha os alertas (exceto .alert-secondary, usado como "nota informativa") depois de 4s.
    function autoFecharAlertas() {
        if (typeof bootstrap === "undefined") return;
        setTimeout(function () {
            document
                .querySelectorAll(".alert:not(.alert-secondary)")
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

    document.addEventListener("DOMContentLoaded", autoFecharAlertas);
})();
