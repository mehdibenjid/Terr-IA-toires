#!/usr/bin/env python3
"""
Analyseur de délibérations municipales françaises
Adapté du code média existant - spécialisé GRDF
"""

import requests
import json
from datetime import datetime
import time
import os
import re
import pymupdf as fitz 

class DeliberationsAnalyzer:
    def __init__(self):
        self.results = []
        # Mots-clés stratégiques GRDF
        self.keywords_grdf = [
            "GRDF", "biogaz", "bioGNV", "Station GNV", "gaz naturel", "gaz vert",
            "méthanisation", "méthaniseur", "méthanation", "gazéification",
            "politique énergétique", "EnR", "énergie renouvelable",
            "réseau de chaleur", "réhabilitation de logements", "éco-construction",
            "éco-habitat", "aménagement urbain", "ANRU", "NPNRU", 
            "rénovation urbaine", "renouvellement urbain", "bailleurs sociaux",
            "Travaux publics", "Valorisation de déchets", "valorisation énergétique", "hydrogène"
        ]
    
    def extract_text_from_pdf(self, pdf_url, max_paragraphs=5):
        """Extrait le texte d'un PDF de délibération - ADAPTATION CLÉ"""
        print(f"📄 Extraction PDF: {pdf_url[:60]}...")
        
        try:
            # Téléchargement du PDF
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; GRDF-Analyzer/1.0)'
            }
            response = requests.get(pdf_url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                # Sauvegarde temporaire
                temp_pdf = "temp_deliberation.pdf"
                with open(temp_pdf, 'wb') as f:
                    f.write(response.content)
                
                # Extraction texte avec PyMuPDF
                doc = fitz.open(temp_pdf)
                text = ""
                for page in doc:
                    text += page.get_text() + "\n"
                doc.close()
                
                # Nettoyage
                text = re.sub(r'\n+', '\n', text)
                text = re.sub(r' +', ' ', text)
                
                # Découpage en paragraphes significatifs
                paragraphs = [p.strip() for p in text.split('\n\n') if len(p.strip()) > 100]
                
                # Recherche des paragraphes pertinents
                matching_paragraphs = []
                for para in paragraphs:
                    para_lower = para.lower()
                    found_keywords = [kw for kw in self.keywords_grdf if kw.lower() in para_lower]
                    
                    if found_keywords:
                        # Surligner les mots-clés
                        highlighted_para = para
                        for keyword in found_keywords:
                            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
                            highlighted_para = pattern.sub(f"**{keyword.upper()}**", highlighted_para)
                        
                        matching_paragraphs.append({
                            'text': highlighted_para[:800] + '...' if len(highlighted_para) > 800 else highlighted_para,
                            'keywords_found': found_keywords,
                            'relevance_score': len(found_keywords) * 10,
                            'context': self._extract_context(para, found_keywords[0])
                        })
                        
                        if len(matching_paragraphs) >= max_paragraphs:
                            break
                
                # Nettoyage
                if os.path.exists(temp_pdf):
                    os.remove(temp_pdf)
                
                print(f"✅ PDF: {len(matching_paragraphs)} paragraphes pertinents")
                return matching_paragraphs
                
            else:
                print(f"❌ Erreur HTTP {response.status_code}")
                
        except Exception as e:
            print(f"❌ Erreur extraction PDF: {str(e)[:100]}")
        
        return []

    def _extract_context(self, text, keyword, words_around=30):
        """Extrait le contexte autour du mot-clé"""
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        match = pattern.search(text)
        
        if match:
            start = max(0, match.start() - words_around * 6)  # ~30 mots avant
            end = min(len(text), match.end() + words_around * 6)  # ~30 mots après
            return text[start:end].strip()
        return ""

    def search_toulouse_deliberations(self, max_results=5):
        """Recherche les délibérations de Toulouse - VOTRE CODE ADAPTÉ"""
        print("🔍 Recherche délibérations Toulouse...")
        
        try:
            # URL des délibérations de Toulouse (à adapter selon la structure réelle)
            base_url = "https://www.toulouse.fr"
            deliberations_url = f"{base_url}/web/transparence/deliberations-du-conseil-municipal"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; GRDF-Analyzer/1.0)'
            }
            
            response = requests.get(deliberations_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # Extraction des liens PDF - méthode simplifiée
                pdf_links = re.findall(r'href="([^"]*\.pdf[^"]*)"', response.text, re.IGNORECASE)
                
                results = []
                deliberation_count = 0
                
                for pdf_link in pdf_links:
                    if 'deliberation' in pdf_link.lower() and deliberation_count < max_results:
                        full_url = pdf_link if pdf_link.startswith('http') else f"{base_url}{pdf_link}"
                        
                        # Extraction du titre depuis l'URL
                        title = os.path.basename(pdf_link).replace('.pdf', '').replace('_', ' ').title()
                        
                        result = {
                            'ville': 'Toulouse',
                            'title': f"Délibération - {title}",
                            'url': full_url,
                            'date': datetime.now().strftime("%Y-%m-%d"),
                            'source': 'Mairie de Toulouse'
                        }
                        
                        # Extraction du contenu PDF
                        extracted_content = self.extract_text_from_pdf(full_url)
                        result['extracted_paragraphs'] = extracted_content
                        
                        if extracted_content:  # Ne garder que si contenu pertinent
                            results.append(result)
                            deliberation_count += 1
                        
                        time.sleep(1)  # Pause entre téléchargements
                
                print(f"✅ Toulouse: {len(results)} délibérations trouvées")
                return results
            else:
                print(f"❌ Toulouse: erreur {response.status_code}")
                
        except Exception as e:
            print(f"❌ Toulouse: {str(e)}")
            
        return []

    def search_paris_deliberations(self, max_results=3):
        """Recherche les délibérations de Paris via Open Data"""
        print("🔍 Recherche délibérations Paris...")
        
        try:
            # API Open Data Paris
            url = "https://opendata.paris.fr/api/records/1.0/search/"
            params = {
                'dataset': 'deliberations-du-conseil-de-paris',
                'rows': max_results,
                'sort': '-date'
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                results = []
                
                for record in data.get('records', []):
                    fields = record['fields']
                    pdf_url = fields.get('url', '')
                    
                    if pdf_url and pdf_url.endswith('.pdf'):
                        result = {
                            'ville': 'Paris',
                            'title': fields.get('objet', 'Délibération Paris'),
                            'url': pdf_url,
                            'date': fields.get('date', 'Date inconnue'),
                            'source': 'Open Data Paris'
                        }
                        
                        # Extraction du contenu
                        extracted_content = self.extract_text_from_pdf(pdf_url)
                        result['extracted_paragraphs'] = extracted_content
                        
                        if extracted_content:
                            results.append(result)
                            time.sleep(1)
                
                print(f"✅ Paris: {len(results)} délibérations trouvées")
                return results
            else:
                print(f"❌ Paris: erreur {response.status_code}")
                
        except Exception as e:
            print(f"❌ Paris: {str(e)}")
            
        return []

    def search_lyon_deliberations(self, max_results=3):
        """Recherche les délibérations de Lyon"""
        print("🔍 Recherche délibérations Lyon...")
        
        try:
            # URL de test pour Lyon
            test_urls = [
                "https://www.lyon.fr/sites/lyonfr/files/deliberation_exemple_1.pdf",
                "https://www.lyon.fr/sites/lyonfr/files/deliberation_exemple_2.pdf"
            ]
            
            results = []
            
            for i, pdf_url in enumerate(test_urls[:max_results]):
                try:
                    result = {
                        'ville': 'Lyon',
                        'title': f'Délibération Lyon #{i+1}',
                        'url': pdf_url,
                        'date': '2024',
                        'source': 'Mairie de Lyon'
                    }
                    
                    # Extraction du contenu (simulé pour la démo)
                    extracted_content = self.extract_text_from_pdf(pdf_url)
                    result['extracted_paragraphs'] = extracted_content
                    
                    if extracted_content:
                        results.append(result)
                    
                    time.sleep(1)
                    
                except Exception as e:
                    print(f"❌ Erreur Lyon {pdf_url}: {e}")
                    continue
            
            print(f"✅ Lyon: {len(results)} délibérations trouvées")
            return results
            
        except Exception as e:
            print(f"❌ Lyon: {str(e)}")
            
        return []

    def analyze_opportunities(self, extracted_paragraphs, ville):
        """Analyse les opportunités GRDF basées sur le contenu"""
        opportunities = []
        
        if not extracted_paragraphs:
            return opportunities
        
        # Concaténer tout le texte pour analyse globale
        all_text = " ".join([p['text'] for p in extracted_paragraphs])
        all_text_lower = all_text.lower()
        
        # Détection d'opportunités par mots-clés
        if any(kw in all_text_lower for kw in ["méthanisation", "méthaniseur"]):
            opportunities.append({
                'type': 'Méthanisation',
                'description': f'Projet de méthanisation identifié à {ville}',
                'urgence': 'Élevée' if 'urgence' in all_text_lower else 'Moyenne',
                'potentiel': 'Fort'
            })
        
        if any(kw in all_text_lower for kw in ["bioGNV", "station GNV"]):
            opportunities.append({
                'type': 'Mobilité durable',
                'description': f'Opportunité station bioGNV à {ville}',
                'urgence': 'Moyenne',
                'potentiel': 'Élevé'
            })
        
        if any(kw in all_text_lower for kw in ["ANRU", "rénovation urbaine"]):
            opportunities.append({
                'type': 'Aménagement urbain',
                'description': f'Projet ANRU à {ville} - éco-construction possible',
                'urgence': 'Moyenne',
                'potentiel': 'Modéré'
            })
        
        if any(kw in all_text_lower for kw in ["réseau de chaleur", "chaleur urbain"]):
            opportunities.append({
                'type': 'Réseau de chaleur',
                'description': f'Développement réseau de chaleur à {ville}',
                'urgence': 'Forte' if 'appel' in all_text_lower else 'Moyenne',
                'potentiel': 'Élevé'
            })
        
        return opportunities

    def search_all_cities(self):
        """Lance la recherche sur toutes les villes"""
        print(f"\n🚀 ANALYSE DES DÉLIBÉRATIONS MUNICIPALES")
        print("="*60)
        print("🎯 Mots-clés GRDF activés:")
        print(", ".join(self.keywords_grdf[:8]) + "...")
        print("="*60)
        
        all_results = []
        
        # Recherche par ville
        search_functions = [
            ('Toulouse', self.search_toulouse_deliberations),
            ('Paris', self.search_paris_deliberations),
            ('Lyon', self.search_lyon_deliberations),
        ]
        
        for ville_name, search_func in search_functions:
            try:
                print(f"\n🏙️  Recherche {ville_name}...")
                results = search_func()
                
                # Analyse des opportunités pour chaque résultat
                for result in results:
                    result['opportunites'] = self.analyze_opportunities(
                        result.get('extracted_paragraphs', []), 
                        result['ville']
                    )
                    result['score_global'] = sum(
                        p['relevance_score'] for p in result.get('extracted_paragraphs', [])
                    )
                
                all_results.extend(results)
                time.sleep(2)  # Pause entre villes
                
            except Exception as e:
                print(f"❌ Erreur {ville_name}: {e}")
                continue
        
        return all_results

    def save_and_display(self, results):
        """Sauvegarde et affiche les résultats - VOTRE FORMAT ADAPTÉ"""
        if not results:
            print("\n❌ Aucune délibération pertinente trouvée.")
            return
        
        print(f"\n📊 RÉSULTATS DÉLIBÉRATIONS: {len(results)}")
        print("="*70)
        
        for i, result in enumerate(results, 1):
            print(f"\n{i}. 🏛️  {result['ville']} - {result['title']}")
            print(f"   🔗 {result['url']}")
            print(f"   📅 {result['date']} | 📊 Score: {result.get('score_global', 0)}")
            
            # Affichage des opportunités
            if result.get('opportunites'):
                print(f"\n   💼 OPPORTUNITÉS IDENTIFIÉES:")
                for opp in result['opportunites']:
                    print(f"   ✅ {opp['type']}: {opp['description']}")
                    print(f"      🚨 Urgence: {opp['urgence']} | 📈 Potentiel: {opp['potentiel']}")
            
            # Affichage des extraits
            if result.get('extracted_paragraphs'):
                print(f"\n   📄 EXTRAITS PERTINENTS:")
                print("   " + "─" * 60)
                
                for j, para in enumerate(result['extracted_paragraphs'][:3], 1):
                    print(f"\n   📋 Extrait {j}:")
                    print(f"   🎯 Mots-clés: {', '.join(para['keywords_found'])}")
                    print(f"   ⭐ Pertinence: {para['relevance_score']}/100")
                    print("   ┌" + "─" * 58 + "┐")
                    
                    # Affichage formaté du texte
                    lines = para['text'].split('. ')
                    for line in lines[:3]:  # 3 premières phrases
                        if line.strip():
                            # Découpage pour affichage propre
                            words = line.strip().split()
                            current_line = ""
                            for word in words:
                                if len(current_line + word) < 55:
                                    current_line += word + " "
                                else:
                                    if current_line:
                                        print(f"   │ {current_line.strip():<55} │")
                                    current_line = word + " "
                            if current_line:
                                print(f"   │ {current_line.strip():<55} │")
                    
                    print("   └" + "─" * 58 + "┘")
            
            print("   " + "=" * 60)
        
        # Sauvegarde JSON
        filename = f"resultats_deliberations_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump({
                    'analyse_date': datetime.now().isoformat(),
                    'total_deliberations': len(results),
                    'mots_cles_recherches': self.keywords_grdf,
                    'resultats': results
                }, f, ensure_ascii=False, indent=2)
            
            print(f"\n💾 SAUVEGARDÉ: {os.path.abspath(filename)}")
            
        except Exception as e:
            print(f"❌ Erreur sauvegarde: {e}")

def main():
    print("🔍 ANALYSEUR DE DÉLIBÉRATIONS GRDF")
    print("="*50)
    print("🏛️  Version spécialisée pour les collectivités françaises")
    print("🎯 Détection automatique des opportunités GRDF")
    print("="*50)
    
    analyzer = DeliberationsAnalyzer()
    
    while True:
        print("\nOptions:")
        print("1. Analyser les délibérations (automatique)")
        print("2. Tester une URL PDF spécifique")
        print("3. Quitter")
        
        choix = input("\nVotre choix (1-3): ").strip()
        
        if choix == '3':
            print("👋 Au revoir!")
            break
        elif choix == '1':
            # Analyse automatique
            results = analyzer.search_all_cities()
            analyzer.save_and_display(results)
        elif choix == '2':
            # Test manuel d'une URL
            url = input("URL du PDF à analyser: ").strip()
            if url:
                content = analyzer.extract_text_from_pdf(url)
                if content:
                    print(f"✅ {len(content)} paragraphes pertinents trouvés!")
                    for i, para in enumerate(content, 1):
                        print(f"\n--- Extrait {i} ---")
                        print(para['text'][:500] + "...")
                else:
                    print("❌ Aucun contenu pertinent trouvé")
        else:
            print("❌ Choix invalide")

if __name__ == "__main__":
    main()