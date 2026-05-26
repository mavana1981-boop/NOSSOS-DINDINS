{% extends "base.html" %}
{% block title %}{{ 'Editar' if expense else 'Novo' }} gasto{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title-wrap">
    <h1>{{ 'Editar' if expense else 'Novo' }} gasto</h1>
    <p>Registre uma despesa e defina como ela é compartilhada.</p>
  </div>
  <a href="{{ url_for('expenses.list_expenses') }}" class="btn btn-ghost">← Voltar</a>
</div>

<div class="card" style="max-width:780px;">
  <form method="POST" id="expense-form">

    <div class="form-group">
      <label class="form-label">Descrição *</label>
      <input class="form-control" name="description" required
             value="{{ expense.description if expense }}"
             placeholder="ex: Corte de cabelo, Supermercado, Conta de luz">
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Valor total (R$) *</label>
        <input class="form-control mono" name="amount" id="amount-input" required inputmode="decimal"
               value="{{ expense.amount if expense }}"
               placeholder="0,00">
      </div>
      <div class="form-group">
        <label class="form-label">Data *</label>
        <input class="form-control" type="date" name="spent_at"
               value="{{ expense.spent_at.isoformat() if expense else '' }}">
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Categoria</label>
        <select class="form-control" name="category">
          {% set cats = ['Alimentação', 'Transporte', 'Saúde', 'Educação', 'Moradia', 'Lazer', 'Vestuário', 'Beleza', 'Serviços', 'Contas', 'Outros'] %}
          {% for c in cats %}
            <option {% if expense and expense.category == c %}selected{% endif %}>{{ c }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Quem pagou (cartão usado) *</label>
        <select class="form-control" name="payer_id" {% if not current_user.is_admin %}disabled{% endif %}>
          {% for u in users %}
            <option value="{{ u.id }}"
              {% if expense and expense.payer_id == u.id %}selected
              {% elif not expense and u.id == current_user.id %}selected{% endif %}>
              {{ u.full_name }}{% if u.id == current_user.id %} (você){% endif %}
            </option>
          {% endfor %}
        </select>
        {% if not current_user.is_admin %}
        <input type="hidden" name="payer_id" value="{{ current_user.id }}">
        <div class="form-help">Só admins podem registrar gastos em nome de outro usuário.</div>
        {% endif %}
      </div>
    </div>

    <div class="divider"></div>

    <h3 style="margin-bottom:12px;">Compartilhamento</h3>

    <div class="form-group">
      <label class="form-label">Modo</label>
      <div class="grid grid-3" style="gap:12px;">
        <label class="form-check share-option" data-mode="solo">
          <input type="radio" name="share_mode" value="solo"
                 {% if not expense or expense.share_mode == 'solo' %}checked{% endif %}>
          <div>
            <strong>Solo</strong>
            <div class="text-faint text-small">Só o pagador absorve.</div>
          </div>
        </label>
        <label class="form-check share-option" data-mode="integral">
          <input type="radio" name="share_mode" value="integral"
                 {% if expense and expense.share_mode == 'integral' %}checked{% endif %}>
          <div>
            <strong>Repasse integral</strong>
            <div class="text-faint text-small">Um paga, outro deve tudo.</div>
          </div>
        </label>
        <label class="form-check share-option" data-mode="split">
          <input type="radio" name="share_mode" value="split"
                 {% if expense and expense.share_mode == 'split' %}checked{% endif %}>
          <div>
            <strong>Dividido</strong>
            <div class="text-faint text-small">Valor/% personalizado por pessoa.</div>
          </div>
        </label>
      </div>
    </div>

    <!-- Modo integral: escolher devedor -->
    <div id="mode-integral" class="form-group" style="display:none;">
      <label class="form-label">Devedor *</label>
      <select class="form-control" name="debtor_id">
        <option value="">— selecione —</option>
        {% for u in users %}
          {% if expense %}
            {% set sel_debtor = (expense.shares|length == 1 and expense.shares[0].user_id == u.id and u.id != expense.payer_id) %}
          {% else %}
            {% set sel_debtor = False %}
          {% endif %}
          <option value="{{ u.id }}" {% if sel_debtor %}selected{% endif %}>
            {{ u.full_name }}{% if u.id == current_user.id %} (você){% endif %}
          </option>
        {% endfor %}
      </select>
      <div class="form-help">Esta pessoa ficará devendo o valor total para o pagador.</div>
    </div>

    <!-- Modo split: matriz de valores -->
    <div id="mode-split" style="display:none;">
      <div class="form-help mb-2">
        Defina o valor que cada pessoa deve. Use o botão para dividir igualmente.
      </div>
      <div style="background:var(--bg);border-radius:var(--radius);padding:14px;">
        {% for u in users %}
          {% set existing_share = namespace(amount='') %}
          {% if expense %}
            {% for s in expense.shares %}
              {% if s.user_id == u.id %}{% set existing_share.amount = s.share_amount %}{% endif %}
            {% endfor %}
          {% endif %}
          <div class="share-row">
            <div>
              {% if u.photo %}
                <img src="{{ u.photo_url }}" class="avatar" style="width:28px;height:28px;">
              {% else %}
                <div class="avatar-fallback" style="width:28px;height:28px;font-size:0.78rem;">{{ u.full_name[0]|upper }}</div>
              {% endif %}
            </div>
            <div>
              <strong>{{ u.full_name }}</strong>
              {% if u.id == current_user.id %}<span class="text-faint text-small">(você)</span>{% endif %}
            </div>
            <input class="form-control mono split-input" name="share_user_{{ u.id }}"
                   data-uid="{{ u.id }}" inputmode="decimal"
                   value="{{ existing_share.amount }}" placeholder="0,00">
          </div>
        {% endfor %}
      </div>
      <div class="flex flex-gap mt-2">
        <button type="button" class="btn btn-ghost btn-sm" id="btn-split-equal">Dividir igualmente</button>
        <div class="text-small text-dim flex" style="margin-left:auto;align-items:center;">
          Soma: <strong id="split-sum" class="mono" style="margin-left:6px;">R$ 0,00</strong>
        </div>
      </div>
    </div>

    <div class="form-group mt-3">
      <label class="form-label">Observações</label>
      <textarea class="form-control" name="notes">{{ expense.notes if expense }}</textarea>
    </div>

    <div class="divider"></div>
    <div class="flex flex-gap">
      <button class="btn btn-primary">Salvar gasto</button>
      <a href="{{ url_for('expenses.list_expenses') }}" class="btn btn-ghost">Cancelar</a>
    </div>
  </form>
</div>

<script>
(function() {
  const radios = document.querySelectorAll('input[name="share_mode"]');
  const integral = document.getElementById('mode-integral');
  const split = document.getElementById('mode-split');
  const amountInput = document.getElementById('amount-input');
  const splitInputs = document.querySelectorAll('.split-input');
  const splitSum = document.getElementById('split-sum');
  const btnEqual = document.getElementById('btn-split-equal');

  function parseBR(s) {
    if (!s) return 0;
    return parseFloat(String(s).replace(/\./g, '').replace(',', '.')) || 0;
  }
  function fmtBR(v) {
    return 'R$ ' + v.toFixed(2).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }
  function updateSum() {
    let total = 0;
    splitInputs.forEach(i => total += parseBR(i.value));
    splitSum.textContent = fmtBR(total);
    const target = parseBR(amountInput.value);
    splitSum.style.color = Math.abs(total - target) < 0.01 ? 'var(--green)' : 'var(--red)';
  }
  function updateMode() {
    const mode = document.querySelector('input[name="share_mode"]:checked').value;
    integral.style.display = mode === 'integral' ? 'block' : 'none';
    split.style.display = mode === 'split' ? 'block' : 'none';
  }
  radios.forEach(r => r.addEventListener('change', updateMode));
  splitInputs.forEach(i => i.addEventListener('input', updateSum));
  amountInput.addEventListener('input', updateSum);

  btnEqual.addEventListener('click', () => {
    const total = parseBR(amountInput.value);
    const n = splitInputs.length;
    if (!total || !n) return;
    const each = Math.floor((total / n) * 100) / 100;
    const remainder = +(total - each * n).toFixed(2);
    splitInputs.forEach((inp, idx) => {
      let v = each;
      if (idx === 0) v = +(each + remainder).toFixed(2);
      inp.value = v.toFixed(2).replace('.', ',');
    });
    updateSum();
  });

  updateMode();
  updateSum();
})();
</script>

{% endblock %}
