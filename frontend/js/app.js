// Frontend Authentication Handler for Khin Ticket

const isLocalDev = (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') && window.location.port !== '5000';
const API_URL = isLocalDev 
    ? `${window.location.protocol}//${window.location.hostname}:5000` 
    : window.location.origin;

function formatApiError(error) {
    if (error && (error.message === 'Failed to fetch' || error.name === 'TypeError')) {
        return isLocalDev
            ? 'Cannot connect to backend server. Please make sure the Python server is running on port 5000 (run: python run.py).'
            : 'Cannot connect to backend server. Please verify your connection or service status.';
    }
    return error.message || 'An unexpected error occurred.';
}

document.addEventListener('DOMContentLoaded', () => {
    // --- Container Elements ---
    const signinContainer = document.getElementById('signin-container');
    const signupContainer = document.getElementById('signup-container');
    const signupPasswordContainer = document.getElementById('signup-password-container');

    // --- Switch Links & Navigation Buttons ---
    const linkToSignup = document.getElementById('link-to-signup');
    const linkToSignin = document.getElementById('link-to-signin');
    const linkStep2ToSignin = document.getElementById('link-step2-to-signin');
    const btnBackToStep1 = document.getElementById('btn-back-to-step1');

    // --- State Storage for 2-step Signup ---
    let pendingSignupData = null;

    // View Switching Functions
    function showSignIn() {
        if (signinContainer) signinContainer.classList.add('active');
        if (signupContainer) signupContainer.classList.remove('active');
        if (signupPasswordContainer) signupPasswordContainer.classList.remove('active');
    }

    function showSignUpStep1() {
        if (signinContainer) signinContainer.classList.remove('active');
        if (signupContainer) signupContainer.classList.add('active');
        if (signupPasswordContainer) signupPasswordContainer.classList.remove('active');
    }

    function showSignUpStep2() {
        if (signinContainer) signinContainer.classList.remove('active');
        if (signupContainer) signupContainer.classList.remove('active');
        if (signupPasswordContainer) signupPasswordContainer.classList.add('active');
    }

    // Switch Event Listeners
    if (linkToSignup) {
        linkToSignup.addEventListener('click', (e) => {
            e.preventDefault();
            showSignUpStep1();
            clearForms();
            pendingSignupData = null;
        });
    }

    if (linkToSignin) {
        linkToSignin.addEventListener('click', (e) => {
            e.preventDefault();
            showSignIn();
            clearForms();
            pendingSignupData = null;
        });
    }

    if (linkStep2ToSignin) {
        linkStep2ToSignin.addEventListener('click', (e) => {
            e.preventDefault();
            showSignIn();
            clearForms();
            pendingSignupData = null;
        });
    }

    if (btnBackToStep1) {
        btnBackToStep1.addEventListener('click', (e) => {
            e.preventDefault();
            showSignUpStep1();
        });
    }

    // --- Password Visibility Toggles ---
    setupPasswordToggle('signin-password', 'toggle-signin-password');
    setupPasswordToggle('signup-password', 'toggle-signup-password');
    setupPasswordToggle('signup-confirm-password', 'toggle-signup-confirm-password');

    // --- Form Elements ---
    const signinForm = document.getElementById('signin-form');
    const signupForm = document.getElementById('signup-form');
    const passwordForm = document.getElementById('password-form');

    // ==========================================
    // 1. SIGN IN HANDLER
    // ==========================================
    if (signinForm) {
        signinForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const emailInput = document.getElementById('signin-email');
            const passwordInput = document.getElementById('signin-password');
            const btnSignin = document.getElementById('btn-signin');

            const email = emailInput ? emailInput.value.trim().toLowerCase() : '';
            const password = passwordInput ? passwordInput.value : '';

            if (!email) {
                showToast('Please enter your email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!validateEmail(email)) {
                showToast('Please enter a valid email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!password) {
                showToast('Please enter your password.', 'error');
                if (passwordInput) passwordInput.focus();
                return;
            }

            try {
                setLoading(btnSignin, true, 'Signing In...');
                
                const response = await fetch(`${API_URL}/api/auth/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });

                let data;
                try {
                    data = await response.json();
                } catch (jsonErr) {
                    throw new Error(`Failed to parse server response (Status ${response.status}). Please verify the backend service is running.`);
                }

                if (!response.ok) {
                    throw new Error(data.detail || 'Authentication failed. Please check your credentials.');
                }

                // Store Token & User Info
                localStorage.setItem('access_token', data.access_token);
                if (data.user) {
                    localStorage.setItem('user_role', data.user.role || 'employee');
                    localStorage.setItem('user_name', data.user.full_name || '');
                    localStorage.setItem('user_email', data.user.email || '');
                    localStorage.setItem('user_department', data.user.department || 'General');
                    localStorage.setItem('user_position', data.user.position || 'Employee');
                    localStorage.setItem('can_manage_departments', data.user.can_manage_departments ? 'true' : 'false');
                }
                showToast('Sign-in successful! Redirecting...', 'success');
                
                // Role-based routing
                setTimeout(() => {
                    const role = data.user ? (data.user.role || 'employee') : 'employee';
                    const staffRoles = ['super_admin', 'admin', 'tech_member', 'agent', 'dept_agent'];
                    if (staffRoles.includes(role)) {
                        window.location.href = 'dashboard.html';
                    } else {
                        window.location.href = 'portal.html';
                    }
                }, 1000);

            } catch (error) {
                console.error('Sign-in error:', error);
                showToast(formatApiError(error), 'error');
                setLoading(btnSignin, false, 'Sign In');
            }
        });
    }

    // ==========================================
    // 2. SIGN UP STEP 1 HANDLER (Profile & Role)
    // ==========================================
    if (signupForm) {
        signupForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const fullnameInput = document.getElementById('signup-fullname');
            const emailInput = document.getElementById('signup-email');
            const departmentInput = document.getElementById('signup-department');
            const positionInput = document.getElementById('signup-position');

            const fullName = fullnameInput ? fullnameInput.value.trim() : '';
            const email = emailInput ? emailInput.value.trim().toLowerCase() : '';
            const department = departmentInput ? departmentInput.value.trim() : 'General';
            const position = positionInput ? positionInput.value.trim() : 'Employee';

            if (!fullName) {
                showToast('Please enter your full name.', 'error');
                if (fullnameInput) fullnameInput.focus();
                return;
            }

            if (!email) {
                showToast('Please enter your email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!validateEmail(email)) {
                showToast('Please enter a valid email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!position) {
                showToast('Please enter your position or job title.', 'error');
                if (positionInput) positionInput.focus();
                return;
            }

            // Save Step 1 state
            pendingSignupData = {
                fullName,
                email,
                department,
                position
            };

            // Personalize Step 2 card
            const passwordSubtitle = document.getElementById('password-subtitle');
            if (passwordSubtitle) {
                passwordSubtitle.textContent = `Set personal password for ${email}`;
            }

            // Navigate to Step 2
            showSignUpStep2();
            const signupPasswordInput = document.getElementById('signup-password');
            if (signupPasswordInput) signupPasswordInput.focus();
        });
    }

    // ==========================================
    // 3. SIGN UP STEP 2 HANDLER (Password & Confirmation)
    // ==========================================
    if (passwordForm) {
        passwordForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            if (!pendingSignupData) {
                showToast('Registration session lost. Please complete Step 1 first.', 'error');
                showSignUpStep1();
                return;
            }

            const passwordInput = document.getElementById('signup-password');
            const confirmPasswordInput = document.getElementById('signup-confirm-password');
            const btnCompleteSignup = document.getElementById('btn-complete-signup');

            const password = passwordInput ? passwordInput.value : '';
            const confirmPassword = confirmPasswordInput ? confirmPasswordInput.value : '';

            if (!password) {
                showToast('Please enter an account password.', 'error');
                if (passwordInput) passwordInput.focus();
                return;
            }

            if (password.length < 6) {
                showToast('Password must be at least 6 characters long.', 'error');
                if (passwordInput) passwordInput.focus();
                return;
            }

            if (password !== confirmPassword) {
                showToast('Passwords do not match. Please re-enter and confirm.', 'error');
                if (confirmPasswordInput) {
                    confirmPasswordInput.focus();
                    confirmPasswordInput.select();
                }
                return;
            }

            // Send registration payload
            try {
                setLoading(btnCompleteSignup, true, 'Creating Account...');

                const response = await fetch(`${API_URL}/api/auth/register`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        full_name: pendingSignupData.fullName,
                        email: pendingSignupData.email,
                        department: pendingSignupData.department,
                        position: pendingSignupData.position,
                        password: password
                    })
                });

                let data;
                try {
                    data = await response.json();
                } catch (jsonErr) {
                    throw new Error(`Failed to parse server response (Status ${response.status}). Please verify the backend service is reachable.`);
                }

                if (!response.ok) {
                    throw new Error(data.detail || 'Registration failed. Please try again.');
                }

                // Registration successful! Store tokens and user state
                if (data.access_token) {
                    localStorage.setItem('access_token', data.access_token);
                    if (data.user) {
                        localStorage.setItem('user_role', data.user.role || 'employee');
                        localStorage.setItem('user_name', data.user.full_name || '');
                        localStorage.setItem('user_email', data.user.email || '');
                        localStorage.setItem('user_department', data.user.department || 'General');
                        localStorage.setItem('user_position', data.user.position || 'Employee');
                        localStorage.setItem('can_manage_departments', data.user.can_manage_departments ? 'true' : 'false');
                    }
                    showToast('Account created successfully! Redirecting...', 'success');

                    const userRole = data.user ? (data.user.role || 'employee') : 'employee';
                    setTimeout(() => {
                        const staffRoles = ['super_admin', 'admin', 'tech_member', 'agent', 'dept_agent'];
                        if (staffRoles.includes(userRole)) {
                            window.location.href = 'dashboard.html';
                        } else {
                            window.location.href = 'portal.html';
                        }
                    }, 1000);
                } else {
                    showToast('Account created successfully! Please sign in.', 'success');
                    setTimeout(() => {
                        showSignIn();
                        clearForms();
                        const signinEmailInput = document.getElementById('signin-email');
                        if (signinEmailInput) signinEmailInput.value = pendingSignupData ? pendingSignupData.email : '';
                        setLoading(btnCompleteSignup, false, 'Create Account');
                        pendingSignupData = null;
                    }, 1200);
                }

            } catch (error) {
                console.error('Sign-up error:', error);
                showToast(formatApiError(error), 'error');
                setLoading(btnCompleteSignup, false, 'Create Account');
            }
        });
    }

    // Check query params for session expiration redirect
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('expired') === 'true') {
        showToast('Your session has expired. Please sign in again.', 'info');
        // Clean URL parameter
        window.history.replaceState({}, document.title, window.location.pathname);
    }
});

// --- Utility Helpers ---

function setupPasswordToggle(inputId, buttonId) {
    const input = document.getElementById(inputId);
    const button = document.getElementById(buttonId);
    
    if (!input || !button) return;

    button.addEventListener('click', () => {
        const isPassword = input.getAttribute('type') === 'password';
        const type = isPassword ? 'text' : 'password';
        input.setAttribute('type', type);
        
        const eyeIcon = button.querySelector('.eye-icon');
        if (eyeIcon) {
            if (type === 'text') {
                eyeIcon.innerHTML = `
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                    <line x1="1" y1="1" x2="23" y2="23" />
                `;
            } else {
                eyeIcon.innerHTML = `
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                    <circle cx="12" cy="12" r="3" />
                `;
            }
        }
    });
}

function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

function setLoading(button, isLoading, text) {
    if (!button) return;
    const span = button.querySelector('span');
    if (isLoading) {
        button.disabled = true;
        button.innerHTML = `<span class="spinner"></span> <span>${text}</span>`;
    } else {
        button.disabled = false;
        button.innerHTML = `<span>${text}</span>`;
    }
}

function clearForms() {
    const signinForm = document.getElementById('signin-form');
    const signupForm = document.getElementById('signup-form');
    const passwordForm = document.getElementById('password-form');
    const techPasscodeGroup = document.getElementById('tech-passcode-group');

    if (signinForm) signinForm.reset();
    if (signupForm) signupForm.reset();
    if (passwordForm) passwordForm.reset();
    if (techPasscodeGroup) techPasscodeGroup.style.display = 'none';
}

// Toast Helper
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    // Choose appropriate SVG Icon based on type
    let iconSvg = '';
    if (type === 'success') {
        iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
    } else if (type === 'error') {
        iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
    } else {
        iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
    }

    toast.innerHTML = `${iconSvg} <span>${message}</span>`;
    container.appendChild(toast);
    
    // Auto remove after 4 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
