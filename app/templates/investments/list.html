{% extends "base.html" %}
{% block title %}Investimentos{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title-wrap">
    <h1>Investimentos</h1>
    <p>
      Investido: <strong style="color:var(--blue);">{{ total_invested|brl }}</strong> ·
      Valor atual: <strong style="color:var(--green);">{{ total_current|brl }}</strong> ·
      <span style="color:{{ 'var(--green)' if total_gain >= 0 else 'var(--red)' }};">
        {{ '+' if total_gain >= 0 else '' }}{{ total_gain|brl }}
      </span>
    </p>
  </div>
  <a href="{{ url_for('investments.new_investment') }}" class="btn btn-primary">+ Novo investimento</a>
</div>

<!-- Filtros -->
<div class="card mb-3">
  <form method="GET" class="flex flex-gap" style="flex-wrap:wrap;align-items:flex-end;">
    <div class="form-group mb-0">
      <label class="form-label">Objetivo</label>
      <select class="form-control" name="objective" style="min-width:180px;">
        <option value="">Todos</option>
        {% for o in all_objectives %}
          <option {% if obj_filter == o %}selected{% endif %}>{{ o }}</option>
        {% endfor %}
        {% for o in objectives %}
          {% if o not in all_objectives %}
            <option {% if obj_filter == o %}selected{% endif %}>{{ o }}</option>
          {% endif %}
        {% endfor %}
      </select>
    </div>
    <div class="form-group mb-0">
      <label class="form-label">Categoria</label>
      <select class="form-control" name="category" style="min-width:160px;">
        <option value="">Todas</option>
        {% for c in all_categories %}
          <option {% if cat_filter == c %}selected{% endif %}>{{ c }}</option>
        {% endfor %}
      </select>
    </div>
    <button class="btn btn-ghost">Filtrar</button>
    {% if obj_filter or cat_filter %}
      <a href="{{ url_for('investments.list_investments') }}" class="btn btn-ghost">Limpar</a>
    {% endif %}
  </form>
</div>

{% if by_objective %}
  {% for obj, data in by_objective.items() %}
  <div class="card mb-3">
    <div class="card-title">
      <div>
        <h3 style="margin:0;">{{ obj }}</h3>
        <div class="text-small text-dim">
          {{ data.items|length }} ativo(s) ·
          Investido: <span class="mono">{{ data.total_invested|brl }}</span> ·
          Atual: <span class="mono" style="color:var(--green);">{{ data.total_current|brl }}</span>
          {% set gain = data.total_current - data.total_invested %}
          · <span style="color:{{ 'var(--green)' if gain >= 0 else 'var(--red)' }};">
              {{ '+' if gain >= 0 }}{{ gain|brl }}
            </span>
        </div>
      </div>
      <!-- Barra de progresso do objetivo -->
      {% set pct = [(data.total_current / data.total_invested * 100) if data.total_invested > 0 else 100, 200]|min %}
      <div style="text-align:right;">
        <div class="pct" style="font-family:'Fraunces',serif;font-weight:700;font-size:1.4rem;color:var(--blue);">
          {{ '%.1f'|format(pct) }}%
        </div>
        <div class="text-faint text-small">rendimento</div>
      </div>
    </div>

    <div class="table-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th>Descrição</th>
            <th>Categoria</th>
            <th>Instituição</th>
            <th>Data</th>
            <th class="text-right">Aportado</th>
            <th class="text-right">Valor atual</th>
            <th class="text-right">Ganho/Perda</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for inv in data.items %}
          <tr>
            <td>
              <strong>{{ inv.description }}</strong>
              {% if not inv.is_active %}<span class="badge badge-solo">Encerrado</span>{% endif %}
              {% if inv.notes %}<div class="text-faint text-small">{{ inv.notes }}</div>{% endif %}
            </td>
            <td><span class="badge badge-shared">{{ inv.category }}</span></td>
            <td class="text-dim">{{ inv.institution or '—' }}</td>
            <td class="text-dim">{{ inv.invested_at|data_br }}</td>
            <td class="text-right amount" style="color:var(--blue);">{{ inv.amount|brl }}</td>
            <td class="text-right amount" style="color:var(--green);">{{ (inv.current_value or inv.amount)|brl }}</td>
            <td class="text-right amount">
              {% set g = inv.gain_loss %}
              <span style="color:{{ 'var(--green)' if g >= 0 else 'var(--red)' }};">
                {{ '+' if g >= 0 }}{{ g|brl }}
              </span>
              <div class="text-faint text-small">{{ '%.1f'|format(inv.gain_loss_pct) }}%</div>
            </td>
            <td class="text-right">
              <a href="{{ url_for('investments.edit_investment', inv_id=inv.id) }}" class="btn btn-ghost btn-sm">Editar</a>
              <form method="POST" action="{{ url_for('investments.delete_investment', inv_id=inv.id) }}"
                    style="display:inline;" onsubmit="return confirm('Excluir?');">
                <button class="btn btn-danger btn-sm">×</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endfor %}
{% else %}
  <div class="card">
    <div class="empty-state">
      <h4>Nenhum investimento registrado</h4>
      <p>Comece a registrar seus investimentos e acompanhe o crescimento por objetivo.</p>
      <a href="{{ url_for('investments.new_investment') }}" class="btn btn-primary btn-sm mt-2">Registrar primeiro</a>
    </div>
  </div>
{% endif %}

{% endblock %}
