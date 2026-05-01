#!/usr/bin/env python3
"""
Analyse les logs du bot pour générer un rapport de performance.
À exécuter sur ton VPS/ordi où les logs sont présents.

Usage:
    python3 analyze_logs.py data/logs/dry/btc_range/
    python3 analyze_logs.py data/logs/live/main/
"""

import csv
import json
import sys
from pathlib import Path
from collections import defaultdict
import statistics

def analyze_trades_csv(csv_path: Path):
    """Analyse un fichier CSV de trades."""
    rows = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return None

    # Calculer les métriques
    pnls = []
    wins = 0
    losses = 0
    for row in rows:
        pnl = float(row.get('pnl', 0) or 0)
        pnls.append(pnl)
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

    total_pnl = sum(pnls)
    win_rate = (wins / len(pnls) * 100) if pnls else 0
    avg_win = statistics.mean([p for p in pnls if p > 0]) if wins else 0
    avg_loss = statistics.mean([p for p in pnls if p < 0]) if losses else 0

    return {
        'total_trades': len(pnls),
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'wins': wins,
        'losses': losses,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': abs(avg_win * wins / (avg_loss * losses)) if losses else float('inf'),
    }

def analyze_log_directory(log_dir: Path):
    """Analyse tous les logs dans un répertoire."""
    results = {}

    # Trouver tous les CSV
    csv_files = list(log_dir.glob('*_trades.csv'))

    for csv_file in csv_files:
        symbol = csv_file.name.split('_')[0]
        stats = analyze_trades_csv(csv_file)
        if stats:
            results[symbol] = stats

    # Résumé global
    total_trades = sum(s['total_trades'] for s in results.values())
    total_pnl = sum(s['total_pnl'] for s in results.values())
    total_wins = sum(s['wins'] for s in results.values())
    total_losses = sum(s['losses'] for s in results.values())

    global_stats = {
        'symbols_analyzed': len(results),
        'total_trades': total_trades,
        'total_pnl': total_pnl,
        'global_win_rate': (total_wins / total_trades * 100) if total_trades else 0,
        'total_wins': total_wins,
        'total_losses': total_losses,
    }

    return {
        'global': global_stats,
        'per_symbol': results,
    }

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_logs.py <log_directory>")
        sys.exit(1)

    log_dir = Path(sys.argv[1])
    if not log_dir.exists():
        print(f"Directory not found: {log_dir}")
        sys.exit(1)

    results = analyze_log_directory(log_dir)

    # Afficher le rapport
    print("=" * 60)
    print(f"ANALYSE DES LOGS: {log_dir}")
    print("=" * 60)

    g = results['global']
    print(f"\nGlobal:")
    print(f"  Symboles analysés: {g['symbols_analyzed']}")
    print(f"  Total trades: {g['total_trades']}")
    print(f"  Win rate global: {g['global_win_rate']:.2f}%")
    print(f"  PnL total: {g['total_pnl']:.2f} USDC")
    print(f"  Wins: {g['total_wins']} | Losses: {g['total_losses']}")

    print(f"\nPar symbole:")
    for symbol, stats in sorted(results['per_symbol'].items(), key=lambda x: x[1]['total_pnl'], reverse=True):
        print(f"  {symbol:10s} | Trades: {stats['total_trades']:3d} | Win: {stats['win_rate']:5.1f}% | PnL: {stats['total_pnl']:8.2f} | PF: {stats['profit_factor']:.2f}")

    # Sauvegarder en JSON
    output_file = log_dir / "analysis_report.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nRapport sauvegardé: {output_file}")
