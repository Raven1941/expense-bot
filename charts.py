import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches


def generate_expense_chart(rows: list, total: float, month_name: str, budgets: dict) -> io.BytesIO:
    """
    Generate horizontal bar chart of monthly expenses by category.
    rows: [(category, amount, count), ...]
    budgets: {category: budget_amount}
    Returns BytesIO PNG image.
    """
    if not rows:
        return None

    categories = [r[0] for r in rows]
    amounts    = [r[1] for r in rows]

    # Shorten category labels (remove emoji prefix for cleaner display)
    import re
    labels = []
    for cat in categories:
        clean = re.sub(r'^[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF\s]+', '', cat).strip()
        labels.append(clean if clean else cat)

    fig, ax = plt.subplots(figsize=(9, max(4, len(rows) * 0.6 + 1.5)))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    colors = [
        '#4cc9f0', '#4361ee', '#3a0ca3', '#7209b7',
        '#f72585', '#b5179e', '#560bad', '#480ca8',
        '#3f37c9', '#3a86ff', '#8338ec', '#06d6a0',
    ]

    bars = ax.barh(
        labels, amounts,
        color=[colors[i % len(colors)] for i in range(len(rows))],
        height=0.6, edgecolor='none'
    )

    # Budget lines
    for i, (cat, amt) in enumerate(zip(categories, amounts)):
        budget = budgets.get(cat)
        if budget:
            ax.plot([budget, budget], [i - 0.4, i + 0.4],
                    color='#ff6b6b', linewidth=1.5, linestyle='--', alpha=0.8)

    # Value labels
    for bar, amt in zip(bars, amounts):
        ax.text(bar.get_width() + total * 0.01, bar.get_y() + bar.get_height() / 2,
                f'${amt:.0f}', va='center', color='white', fontsize=8.5, fontweight='bold')

    ax.set_xlabel('CAD', color='#adb5bd', fontsize=9)
    ax.set_title(f'Расходы — {month_name}\nИтого: ${total:.2f} CAD',
                 color='white', fontsize=12, fontweight='bold', pad=12)
    ax.tick_params(colors='#adb5bd', labelsize=9)
    ax.spines['bottom'].set_color('#444')
    ax.spines['left'].set_color('#444')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.xaxis.label.set_color('#adb5bd')

    for label in ax.get_yticklabels():
        label.set_color('white')

    if any(budgets.get(cat) for cat in categories):
        patch = mpatches.Patch(color='#ff6b6b', linestyle='--', label='Бюджет')
        ax.legend(handles=[patch], facecolor='#1a1a2e', labelcolor='white', fontsize=8)

    ax.set_xlim(0, max(amounts) * 1.18)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf
