import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightBlog from 'starlight-blog';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
    site: 'https://seocho.blog',
    integrations: [
        tailwind({ applyBaseStyles: false }),
        starlight({
            title: 'SEOCHO',
            description: 'Ontology-first graph memory and runtime for agents',
            customCss: ['./src/styles/custom.css'],
            plugins: [
                starlightBlog({
                    title: 'Blog',
                    navigation: 'none',
                    recentPostCount: 5,
                    authors: {
                        seocho: {
                            name: 'SEOCHO Core Engine',
                            title: 'System AI',
                        },
                    },
                })
            ],
            social: [
                { icon: 'github', label: 'GitHub', href: 'https://github.com/TTEON/seocho' }
            ],
            sidebar: [
                { label: 'Home', link: '/' },
                {
                    label: 'Docs', items: [
                        { label: 'Introduction', link: '/docs/' },
                        { label: 'Why SEOCHO', link: '/docs/why_seocho/' },
                        { label: 'Quick Start', link: '/docs/quickstart/' },
                        { label: 'Bring Your Data', link: '/docs/apply_your_data/' },
                        { label: 'Python SDK', link: '/docs/python_sdk/' },
                        { label: 'Files and Artifacts', link: '/docs/files_and_artifacts/' },
                        { label: 'Tutorial', link: '/docs/tutorial/' },
                        { label: 'Open Source Playbook', link: '/docs/open_source_playbook/' },
                        { label: 'Architecture', link: '/docs/architecture/' },
                        { label: 'Workflow', link: '/docs/workflow/' },
                        { label: 'Philosophy', link: '/docs/philosophy/' }
                    ]
                },
                {
                    label: 'Python SDK', items: [
                        { label: 'Overview', link: '/sdk/' },
                        { label: 'Getting Started', link: '/sdk/getting-started/' },
                        { label: 'Ontology and Semantic Artifacts', link: '/sdk/ontology-guide/' },
                        { label: 'API Reference', link: '/sdk/api-reference/' },
                        { label: 'Examples', link: '/sdk/examples/' },
                    ]
                },
                { label: 'Examples', link: '/examples/' },
                { label: 'Changelog', link: '/changelog/' },
                { label: 'Community', link: '/community/' },
            ],
            head: [
                { tag: 'link', attrs: { rel: 'icon', href: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>✨</text></svg>' } },
                { tag: 'meta', attrs: { property: 'og:image', content: 'https://seocho.blog/og-image.jpg' } },
                { tag: 'meta', attrs: { property: 'og:type', content: 'website' } },
            ],
        }),
    ],
});
