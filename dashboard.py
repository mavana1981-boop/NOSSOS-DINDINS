{% extends "base.html" %}
{% block title %}{{ 'Editar' if income else 'Nova' }} renda{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title-wrap">
    <h1>{{ 'Editar' if income else 'Nova' }} renda</h1>
    <p>Registre uma entrada — salário, bônus, freelance, rendimentos.</p>
  </div>
  <a href="{{ url_for('income.list_incomes') }}" class="btn btn-ghost">← Voltar</a>
</div>

<div class="card" style="max-width:680px;">
  <form method="POST">
    <div class="form-group">
      <label class="form-label">Descrição *</label>
      <input class="form-control" name="description" required
             value="{{ income.description if income }}"
             placeholder="ex: Salário, Pró-labore, Aluguel recebido">
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Valor (R$) *</label>
        <input class="form-control mono" name="amount" required inputmode="decimal"
               value="{{ income.amount if income }}"
               placeholder="0,00">
      </div>
      <div class="form-group">
        <label class="form-label">Data *</label>
        <input class="form-control" type="date" name="received_at"
               value="{{ income.received_at.isoformat() if income else '' }}">
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Categoria</label>
        <select class="form-control" name="category">
          {% set cats = ['Salário', 'Pró-labore', 'Freelance', 'Investimentos', 'Aluguel', 'Bônus', 'Outros'] %}
          {% for c in cats %}
            <option {% if income and income.category == c %}selected{% endif %}>{{ c }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Recorrente</label>
        <label class="form-check">
          <input type="checkbox" name="is_recurring" {% if income and income.is_recurring %}checked{% endif %}>
          <span>Esta renda se repete mensalmente</span>
        </label>
      </div>
    </div>

    <div class="form-group">
      <label class="form-label">Observações</label>
      <textarea class="form-control" name="notes">{{ income.notes if income }}</textarea>
    </div>

    <div class="divider"></div>
    <div class="flex flex-gap">
      <button class="btn btn-primary">Salvar</button>
      <a href="{{ url_for('income.list_incomes') }}" class="btn btn-ghost">Cancelar</a>
    </div>
  </form>
</div>

{% endblock %}
