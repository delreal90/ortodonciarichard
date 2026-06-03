/* ═══════════════════════════════════════════
   VIDEO — reproducción al 50% de velocidad
═══════════════════════════════════════════ */
const heroVideo = document.querySelector('.hero-media video');
if (heroVideo) {
    heroVideo.playbackRate = 0.75;
    heroVideo.addEventListener('play', () => { heroVideo.playbackRate = 0.75; });
}

/* ═══════════════════════════════════════════
   DATOS DOCTORES
   TODO: Completar bio y formación de cada doctor
═══════════════════════════════════════════ */
const doctorData = {
    octavio: {
        name:        'Dr. Octavio Del Real S.',
        role:        'Ortodoncista',
        photo:       'images/dr-octavio-del-real.jpeg',
        memberships: ['AAO', 'WFO', 'SORT Chile'],
        bio:         'Ortodoncista con práctica privada exclusiva en ortodoncia. Presidente de la Comisión de Ortodoncia de CONACEO, ex Presidente de la Sociedad de Ortodoncia de Chile (2000–2004) y conferencista nacional e internacional con numerosas publicaciones científicas.',
        education: [
            'Cirujano Dentista — Universidad de Chile (1978)',
            'Especialista en Ortodoncia y Ortopedia Dentomaxilar — Universidad de Chile (1985)',
        ],
        specialties: [
            'Ortodoncia y Ortopedia Dentomaxilar',
            'Profesor Programa de Especialización en Ortodoncia, Universidad de los Andes',
            'Ex Presidente Sociedad de Ortodoncia de Chile (2000–2004)',
            'Presidente Comisión de Ortodoncia de CONACEO',
            'Miembro del Colegio de Cirujano Dentistas de Chile',
        ],
    },
    rodrigo: {
        name:        'Dr. Rodrigo Oyonarte W.',
        role:        'Ortodoncista',
        photo:       'images/dr-rodrigo-oyonarte.jpeg',
        memberships: ['AAO', 'WFO', 'SORT Chile'],
        bio:         'Profesor Titular de la Universidad de los Andes y Director del Programa de Especialización en Ortodoncia. Editor de la Revista Chilena de Ortodoncia, autor de publicaciones científicas nacionales e internacionales y ganador de premios en investigación en Chile y EE.UU.',
        education: [
            'Cirujano Dentista — Universidad de Chile (1996)',
            'Máster en Ciencias y Especialista en Ortodoncia y Ortopedia Dentomaxilar — Universidad de Toronto, Canadá (2002)',
            'Diplomado Medicina Basada en la Evidencia — Facultad de Medicina, Universidad de los Andes (2011)',
        ],
        specialties: [
            'Ortodoncia y Ortopedia Dentomaxilar',
            'Director y Profesor del Programa de Especialización en Ortodoncia, Universidad de los Andes',
            'Editor Revista Chilena de Ortodoncia',
            'Miembro del Colegio de Cirujano Dentistas de Chile',
        ],
    },
    alberto: {
        name:        'Dr. Alberto Del Real V.',
        role:        'Ortodoncista',
        photo:       'images/dr-alberto-del-real.jpeg',
        memberships: ['AAO', 'WFO', 'SORT Chile'],
        bio:         'Ortodoncista con formación en medicina basada en la evidencia y educación en ciencias de la salud. Autor de publicaciones científicas nacionales e internacionales.',
        education: [
            'Cirujano Dentista — Universidad de los Andes (2014)',
            'Especialista en Ortodoncia y Ortopedia Dentomaxilofacial — Universidad de los Andes (2019)',
            'Diplomado Medicina Basada en la Evidencia — Facultad de Medicina, Universidad de los Andes (2015)',
            'Diplomado en Educación en Ciencias de la Salud — Facultad de Medicina, Universidad de Chile (2016)',
        ],
        specialties: [
            'Ortodoncia y Ortopedia Dentomaxilofacial',
            'Miembro del Colegio de Cirujano Dentistas de Chile',
        ],
    },
    patricio: {
        name:        'Dr. Patricio Vial U.',
        role:        'Rehabilitador Oral e Implantólogo',
        photo:       'images/dr-patricio-vial.png',
        memberships: ['Implantología', 'Rehabilitación Oral'],
        bio:         'Especialista en Rehabilitación Oral e Implantología con amplia trayectoria clínica y académica. Coordinador del área quirúrgica del Programa de Especialización en Implantología Buco Máxilo Facial de la UNAB.',
        education: [
            'Cirujano Dentista — Universidad de Chile',
            'Especialista en Rehabilitación Oral — Universidad de Chile',
            'Diplomado en Cirugía de Implantes — Universidad de Chile',
            'Magíster en Pedagogía Universitaria — Universidad Mayor',
        ],
        specialties: [
            'Rehabilitación Oral',
            'Implantología',
            'Coordinador área quirúrgica, Programa de Especialización en Implantología Buco Máxilo Facial — UNAB',
        ],
    },
};

/* ═══════════════════════════════════════════
   NAV — sticky + transparent-to-solid on scroll
═══════════════════════════════════════════ */
const navbar    = document.getElementById('navbar');
const navToggle = document.getElementById('navToggle');
const navLinks  = document.getElementById('navLinks');

window.addEventListener('scroll', () => {
    navbar.classList.toggle('scrolled', window.scrollY > 60);
}, { passive: true });

navToggle.addEventListener('click', () => {
    const open = navLinks.classList.toggle('open');
    navToggle.classList.toggle('open', open);
    navToggle.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú');
});

// Close mobile nav on link click
navLinks.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', () => {
        navLinks.classList.remove('open');
        navToggle.classList.remove('open');
    });
});


/* ═══════════════════════════════════════════
   SMOOTH SCROLL with nav offset
═══════════════════════════════════════════ */
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', e => {
        const target = document.querySelector(anchor.getAttribute('href'));
        if (!target) return;
        e.preventDefault();
        const navH = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--nav-h')) || 80;
        const top  = target.getBoundingClientRect().top + window.scrollY - navH;
        window.scrollTo({ top, behavior: 'smooth' });
    });
});


/* ═══════════════════════════════════════════
   SCROLL REVEAL — Intersection Observer
═══════════════════════════════════════════ */
const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            revealObserver.unobserve(entry.target);
        }
    });
}, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

document.querySelectorAll('.reveal').forEach(el => revealObserver.observe(el));


/* ═══════════════════════════════════════════
   TEAM TABS
═══════════════════════════════════════════ */
const tabBtns     = document.querySelectorAll('.tab-btn');
const tabContents = document.querySelectorAll('.tab-content');

tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        const tabId = btn.dataset.tab;

        tabBtns.forEach(b => b.classList.remove('active'));
        tabContents.forEach(c => c.classList.remove('active'));

        btn.classList.add('active');
        const activeTab = document.getElementById('tab-' + tabId);
        if (activeTab) {
            activeTab.classList.add('active');
            // Re-trigger reveal animations for newly visible cards
            activeTab.querySelectorAll('.reveal').forEach(el => {
                el.classList.remove('visible');
                setTimeout(() => revealObserver.observe(el), 10);
            });
        }
    });
});


/* ═══════════════════════════════════════════
   MODAL DOCTOR
═══════════════════════════════════════════ */
const modal      = document.getElementById('doctorModal');
const modalClose = document.getElementById('modalClose');

function openModal(doctorId) {
    const d = doctorData[doctorId];
    if (!d) return;

    document.getElementById('modalPhoto').src = d.photo;
    document.getElementById('modalPhoto').alt = d.name;
    document.getElementById('modalName').textContent = d.name;
    document.getElementById('modalRole').textContent = d.role;
    document.getElementById('modalBio').textContent  = d.bio;

    const badgesEl = document.getElementById('modalBadges');
    badgesEl.innerHTML = d.memberships.map(m =>
        `<span class="team-badge">${m}</span>`
    ).join('');

    const eduList = document.getElementById('modalEducation');
    eduList.innerHTML = d.education.map(e => `<li>${e}</li>`).join('');

    const spList = document.getElementById('modalSpecialties');
    spList.innerHTML = d.specialties.map(s => `<li>${s}</li>`).join('');

    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    modal.classList.remove('open');
    document.body.style.overflow = '';
}

document.querySelectorAll('.doctor-card').forEach(card => {
    card.addEventListener('click', () => openModal(card.dataset.doctorId));
    card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') openModal(card.dataset.doctorId);
    });
});

modalClose.addEventListener('click', closeModal);
modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

/* ═══════════════════════════════════════════
   PLACEHOLDER INITIALS — inject text from data attr
═══════════════════════════════════════════ */
document.querySelectorAll('.placeholder-photo').forEach(photo => {
    const initials = photo.dataset.initials || '?';
    const circle   = photo.querySelector('.initials-circle');
    if (circle) circle.textContent = initials;
});


/* ═══════════════════════════════════════════
   FAQ TABS
═══════════════════════════════════════════ */
document.querySelectorAll('.faq-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const id = btn.dataset.faq;
        document.querySelectorAll('.faq-tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.faq-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        const target = document.getElementById('faq-' + id);
        if (target) target.classList.add('active');
    });
});

/* ═══════════════════════════════════════════
   ACCORDION
═══════════════════════════════════════════ */
document.querySelectorAll('.acc-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const item = btn.closest('.acc-item');
        const isOpen = item.classList.contains('open');
        // Cierra todos los del mismo accordion
        item.closest('.accordion').querySelectorAll('.acc-item').forEach(i => i.classList.remove('open'));
        // Abre el clickeado (si no estaba abierto)
        if (!isOpen) item.classList.add('open');
    });
});

/* ═══════════════════════════════════════════
   ACTIVE NAV LINK — highlight on scroll
═══════════════════════════════════════════ */
const sections  = document.querySelectorAll('section[id]');
const navALinks = document.querySelectorAll('.nav-link');

const sectionObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            navALinks.forEach(a => a.classList.remove('active'));
            const active = document.querySelector(`.nav-link[href="#${entry.target.id}"]`);
            if (active) active.classList.add('active');
        }
    });
}, { rootMargin: '-40% 0px -55% 0px' });

sections.forEach(s => sectionObserver.observe(s));


/* ═══════════════════════════════════════════
   CONTACT FORM — Web3Forms con feedback visual
═══════════════════════════════════════════ */
const contactForm = document.querySelector('.contact-form');
if (contactForm) {
    contactForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const accessKey = contactForm.querySelector('[name="access_key"]')?.value;

        // Si el access key no está configurado, redirigir a WhatsApp
        if (!accessKey || accessKey === 'YOUR_ACCESS_KEY') {
            const nombre  = document.getElementById('nombre')?.value || '';
            const mensaje = document.getElementById('mensaje')?.value || '';
            const text    = encodeURIComponent(`Hola, soy ${nombre}. ${mensaje}`);
            window.open(`https://wa.me/56933558189?text=${text}`, '_blank');
            return;
        }

        const submitBtn = contactForm.querySelector('[type="submit"]');
        const originalHTML = submitBtn.innerHTML;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Enviando...';
        submitBtn.disabled = true;

        try {
            const data = new FormData(contactForm);
            const res  = await fetch('https://api.web3forms.com/submit', {
                method: 'POST',
                body: data,
            });
            const json = await res.json();

            if (json.success) {
                contactForm.innerHTML = `
                    <div style="text-align:center;padding:40px 20px">
                        <i class="fas fa-circle-check" style="font-size:3rem;color:#4caf50;margin-bottom:16px;display:block"></i>
                        <h3 style="color:var(--navy);margin-bottom:8px">¡Mensaje enviado!</h3>
                        <p style="color:var(--text-mid)">Nos pondremos en contacto contigo a la brevedad.<br>También puedes escribirnos por WhatsApp.</p>
                        <a href="https://wa.me/56933558189" class="btn btn-primary" style="margin-top:20px" target="_blank">
                            <i class="fab fa-whatsapp"></i> Ir a WhatsApp
                        </a>
                    </div>`;
            } else {
                throw new Error(json.message);
            }
        } catch {
            submitBtn.innerHTML = '<i class="fas fa-triangle-exclamation"></i> Error al enviar — intenta por WhatsApp';
            submitBtn.disabled = false;
            submitBtn.style.background = '#e53e3e';
        }
    });
}
