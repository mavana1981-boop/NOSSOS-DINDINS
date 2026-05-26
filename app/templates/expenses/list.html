{% extends "base.html" %}
{% block title %}Gastos{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title-wrap">
    <h1>Gastos</h1>
    <p>
      Você pagou <strong style="color:var(--red);">{{ total_paid|brl }}</strong>
      · Sua cota total: <strong>{{ total_my_share|brl }}</strong>
    </p>
  </div>
  <a href="{{ url_for('expenses.new_expense') }}" class="btn btn-primary">+ Novo gasto</a>
</div>

<div class="card">
  {% if expenses %}
  <div class="table-wrap">
    <table class="tbl">
      <thead>
        <tr>
          <th>Data</th>
          <th>Descrição</th>
          <th>Categoria</th>
          <th>Pagador</th>
          <th>Compartilhamento</th>
          <th class="text-right">Valor</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for e in expenses %}
        <tr>
          <td class="text-dim">{{ e.spent_at|data_br }}</td>
          <td>
            <strong>{{ e.description }}</strong>
            {% if e.notes %}<div class="text-faint text-small">{{ e.notes }}</div>{% endif %}
            {% if e.shares|length > 1 or (e.shares|length == 1 and e.shares[0].user_id != e.payer_id) %}
              <div class="text-small text-dim mt-1">
                {% for s in e.shares %}
                  {{ s.user.full_name.split()[0] }}: <span class="mono">{{ s.share_amount|brl }}</span>{% if not loop.last %} · {% endif %}
                {% endfor %}
              </div>
            {% endif %}
          </td>
          <td><span class="badge badge-solo">{{ e.category }}</span></td>
          <td>
            <div class="flex flex-gap">
              {% if e.payer.photo %}
                <img src="{{ e.payer.photo_url }}" class="avatar" style="width:24px;height:24px;">
              {% else %}
                <div class="avatar-fallback" style="width:24px;height:24px;font-size:0.75rem;">{{ e.payer.full_name[0]|upper }}</div>
              {% endif %}
              <span class="text-small">{{ e.payer.full_name.split()[0] }}</span>
            </div>
          </td>
          <td>
            {% if e.share_mode == 'integral' %}<span class="badge badge-shared">Repasse</span>
            {% elif e.share_mode == 'split' %}<span class="badge badge-shared">Dividido</span>
            {% else %}<span class="badge badge-solo">Solo</span>{% endif %}
          </td>
          <td class="text-right amount">{{ e.amount|brl }}</td>
          <td class="text-right">
            {% if e.payer_id == current_user.id or current_user.is_admin %}
              <a href="{{ url_for('expenses.edit_expense', expense_id=e.id) }}" class="btn btn-ghost btn-sm">Editar</a>
              <form method="POST" action="{{ url_for('expenses.delete_expense', expense_id=e.id) }}"
                    style="display:inline;" onsubmit="return confirm('Excluir este gasto?');">
                <button class="btn btn-danger btn-sm">×</button>
              </form>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
    <div class="empty-state">
      <h4>Nenhum gasto registrado</h4>
      <p>Comece a registrar suas despesas — sozinhas ou compartilhadas.</p>
      <a href="{{ url_for('expenses.new_expense') }}" class="btn btn-primary btn-sm mt-2">Registrar primeiro</a>
    </div>
  {% endif %}
</div>

{% endblock %}
