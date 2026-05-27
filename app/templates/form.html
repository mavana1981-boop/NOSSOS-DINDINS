{% extends "base.html" %}
{% block title %}{{ 'Editar' if inv else 'Novo' }} investimento{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title-wrap">
    <h1>{{ 'Editar' if inv else 'Novo' }} investimento</h1>
    <p>Registre um ativo e associe-o a um objetivo financeiro.</p>
  </div>
  <a href="{{ url_for('investments.list_investments') }}" class="btn btn-ghost">← Voltar</a>
</div>

<div class="card" style="max-width:680px;">
  <form method="POST">
    <div class="form-group">
      <label class="form-label">Descrição *</label>
      <input class="form-control" name="description" required
             value="{{ inv.description if inv }}"
             placeholder="ex: Tesouro IPCA+ 2029, PETR4, CDB Banco X">
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Objetivo *</label>
        <select class="form-control" name="objective">
          {% for o in objectives %}
            <option {% if inv and inv.objective == o %}selected{% endif %}>{{ o }}</option>
          {% endfor %}
        </select>
        <div class="form-help">Ou digita abaixo para criar novo objetivo:</div>
        <input class="form-control mt-1" name="objective_custom" id="obj-custom"
               placeholder="Novo objetivo (substitui seleção acima)"
               style="font-size:0.85rem;">
      </div>
      <div class="form-group">
        <label class="form-label">Categoria</label>
        <select class="form-control" name="category">
          {% for c in categories %}
            <option {% if inv and inv.category == c %}selected{% endif %}>{{ c }}</option>
          {% endfor %}
        </select>
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Valor aportado (R$) *</label>
        <input class="form-control mono" name="amount" required inputmode="decimal"
               value="{% if inv %}{{ '%.2f'|format(inv.amount|float) }}{% endif %}"
               placeholder="0,00">
      </div>
      <div class="form-group">
        <label class="form-label">Valor atual (R$)</label>
        <input class="form-control mono" name="current_value" inputmode="decimal"
               value="{% if inv and inv.current_value %}{{ '%.2f'|format(inv.current_value|float) }}{% endif %}"
               placeholder="Deixe vazio se igual ao aportado">
        <div class="form-help">Atualize manualmente quando quiser registrar o saldo atual.</div>
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Data do aporte *</label>
        <input class="form-control" type="date" name="invested_at"
               value="{{ inv.invested_at.isoformat() if inv else '' }}">
      </div>
      <div class="form-group">
        <label class="form-label">Instituição</label>
        <input class="form-control" name="institution"
               value="{{ inv.institution if inv }}"
               placeholder="ex: XP, Nubank, BTG">
      </div>
    </div>

    <div class="form-group">
      <label class="form-label">Observações</label>
      <textarea class="form-control" name="notes">{{ inv.notes if inv }}</textarea>
    </div>

    <div class="form-group">
      <label class="form-check">
        <input type="checkbox" name="is_active"
               {% if not inv or inv.is_active %}checked{% endif %}>
        <span>Ativo (desmarcado = encerrado/resgatado)</span>
      </label>
    </div>

    <div class="divider"></div>
    <div class="flex flex-gap">
      <button class="btn btn-primary">Salvar</button>
      <a href="{{ url_for('investments.list_investments') }}" class="btn btn-ghost">Cancelar</a>
    </div>
  </form>
</div>

<script>
// Se o usuário digitar um objetivo customizado, ele substitui o select
document.querySelector('input[name="objective_custom"]').addEventListener('input', function() {
  const sel = document.querySelector('select[name="objective"]');
  if (this.value.trim()) {
    sel.name = '_objective_disabled';
    this.name = 'objective';
  } else {
    sel.name = 'objective';
    this.name = 'objective_custom';
  }
});
</script>

{% endblock %}
